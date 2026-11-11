"""Visual builder model <-> pbtx tree.

The model is the JSON shape the frontend builder edits. apply_model writes
back only the differences so untouched config (and comments) is preserved.
"""

from . import paths
from .pbtx import Field, Message, PbtxError, parse, serialize

SOURCE_KEYS = [
    "linux.process_stats",
    "linux.sys_stats",
    "android.surfaceflinger.frametimeline",
    "android.power",
    "android.log",
    "android.packages_list",
    "linux.system_info",
    "android.statsd",
    "track_event",
]

_COMMON_FTRACE_EVENTS = [
    "sched/sched_switch", "sched/sched_wakeup", "sched/sched_wakeup_new",
    "sched/sched_waking", "sched/sched_blocked_reason",
    "sched/sched_process_exit", "sched/sched_process_free",
    "power/cpu_frequency", "power/cpu_idle", "power/gpu_frequency",
    "power/suspend_resume", "power/wakeup_source_activate",
    "power/wakeup_source_deactivate", "task/task_newtask",
    "task/task_rename", "ftrace/print", "kmem/rss_stat",
    "mm_event/mm_event_record", "lowmemorykiller/lowmemory_kill",
    "oom/oom_score_adj_update", "workqueue/*", "vmscan/*",
    "block/block_rq_insert", "block/block_rq_issue",
    "block/block_rq_complete", "raw_syscalls/sys_enter",
    "raw_syscalls/sys_exit", "f2fs/*", "ext4/*", "irq/*", "ipi/*",
    "binder/*", "thermal/*",
]


def unquote(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        body = raw[1:-1]
        out = []
        i = 0
        while i < len(body):
            if body[i] == "\\" and i + 1 < len(body):
                nxt = body[i + 1]
                out.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
                i += 2
            else:
                out.append(body[i])
                i += 1
        return "".join(out)
    return raw


def quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _int(raw, default=None):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _data_sources(msg: Message):
    """Yield (field, config_message, name) for each data_sources entry."""
    for field in msg.get("data_sources"):
        if not isinstance(field.value, Message):
            continue
        config = field.value.first("config")
        if config is None or not isinstance(config.value, Message):
            continue
        name_field = config.value.first("name")
        if name_field is None or isinstance(name_field.value, Message):
            continue
        yield field, config.value, unquote(name_field.value)


def model_from_text(text: str) -> dict:
    msg = parse(text)
    model = {
        "duration_ms": None,
        "buffers": [],
        "ftrace": {"enabled": False, "events": [], "atrace_categories": [],
                   "atrace_apps": [], "buffer_size_kb": None},
        "sources": {key: False for key in SOURCE_KEYS},
    }
    duration = msg.first("duration_ms")
    if duration is not None and not isinstance(duration.value, Message):
        model["duration_ms"] = _int(duration.value)
    for buf in msg.get("buffers"):
        if not isinstance(buf.value, Message):
            continue
        size = buf.value.first("size_kb")
        policy = buf.value.first("fill_policy")
        model["buffers"].append({
            "size_kb": _int(size.value) if size else 0,
            "fill_policy": (unquote(policy.value)
                            if policy and not isinstance(policy.value, Message)
                            else "RING_BUFFER"),
        })
    for _field, config, name in _data_sources(msg):
        if name in model["sources"]:
            model["sources"][name] = True
        if name == "linux.ftrace" and not model["ftrace"]["enabled"]:
            model["ftrace"]["enabled"] = True
            ftrace = config.first("ftrace_config")
            if ftrace is not None and isinstance(ftrace.value, Message):
                cfg = ftrace.value
                model["ftrace"]["events"] = [
                    unquote(f.value) for f in cfg.get("ftrace_events")
                    if not isinstance(f.value, Message)]
                model["ftrace"]["atrace_categories"] = [
                    unquote(f.value) for f in cfg.get("atrace_categories")
                    if not isinstance(f.value, Message)]
                model["ftrace"]["atrace_apps"] = [
                    unquote(f.value) for f in cfg.get("atrace_apps")
                    if not isinstance(f.value, Message)]
                size = cfg.first("buffer_size_kb")
                if size is not None and not isinstance(size.value, Message):
                    model["ftrace"]["buffer_size_kb"] = _int(size.value)
    return model


def _sync_list(cfg: Message, name: str, wanted: list[str]):
    """Sync a repeated string field: keep order of retained values, append
    new ones after the last existing field of the same name, remove the
    rest."""
    wanted = list(dict.fromkeys(wanted))
    want_set = set(wanted)
    seen = set()
    kept = []
    for f in list(cfg.fields):
        if f.name != name:
            continue
        value = unquote(f.value) if not isinstance(f.value, Message) else None
        if value in want_set and value not in seen:
            seen.add(value)
            kept.append(f)
        else:
            cfg.fields.remove(f)
    to_add = [v for v in wanted if v not in seen]
    if not to_add:
        return
    insert_at = len(cfg.fields)
    for i in range(len(cfg.fields) - 1, -1, -1):
        if cfg.fields[i].name == name:
            insert_at = i + 1
            break
    for value in to_add:
        cfg.fields.insert(insert_at, Field(name, quote(value)))
        insert_at += 1


def _default_source(name: str) -> Message:
    config = Message()
    config.add("name", quote(name))
    config.add("target_buffer", "0")
    if name == "linux.process_stats":
        sub = Message()
        sub.add("scan_all_processes_on_start", "true")
        sub.add("proc_stats_poll_ms", "10000")
        config.add("process_stats_config", sub)
    elif name == "linux.sys_stats":
        sub = Message()
        sub.add("psi_period_ms", "100")
        config.add("sys_stats_config", sub)
    elif name == "android.power":
        sub = Message()
        sub.add("battery_poll_ms", "1000")
        config.add("android_power_config", sub)
    elif name == "android.log":
        sub = Message()
        sub.add("log_ids", "LID_DEFAULT")
        config.add("android_log_config", sub)
    data_source = Message()
    data_source.add("config", config)
    return data_source


def _default_ftrace(model: dict) -> Message:
    config = Message()
    config.add("name", quote("linux.ftrace"))
    config.add("target_buffer", "0")
    ftrace = Message()
    for event in model["events"]:
        ftrace.add("ftrace_events", quote(event))
    for cat in model["atrace_categories"]:
        ftrace.add("atrace_categories", quote(cat))
    for app in model["atrace_apps"]:
        ftrace.add("atrace_apps", quote(app))
    if model["buffer_size_kb"] is not None:
        ftrace.add("buffer_size_kb", str(int(model["buffer_size_kb"])))
    config.add("ftrace_config", ftrace)
    data_source = Message()
    data_source.add("config", config)
    return data_source


def apply_model(text: str, model: dict) -> str:
    msg = parse(text)
    old = model_from_text(text)

    if model.get("duration_ms") != old["duration_ms"]:
        if model.get("duration_ms") is None:
            msg.remove("duration_ms")
        else:
            msg.set_scalar("duration_ms", str(int(model["duration_ms"])))

    new_buffers = model.get("buffers", [])
    if new_buffers != old["buffers"]:
        existing = [f for f in msg.get("buffers")
                    if isinstance(f.value, Message)]
        for index, bm in enumerate(new_buffers):
            if index < len(existing):
                existing[index].value.set_scalar(
                    "size_kb", str(int(bm["size_kb"])))
                existing[index].value.set_scalar(
                    "fill_policy", bm["fill_policy"])
            else:
                sub = Message()
                sub.add("size_kb", str(int(bm["size_kb"])))
                sub.add("fill_policy", bm["fill_policy"])
                msg.add("buffers", sub)
        for f in existing[len(new_buffers):]:
            msg.fields.remove(f)

    old_ftrace, new_ftrace = old["ftrace"], model["ftrace"]
    if new_ftrace != old_ftrace:
        ftrace_entries = [(f, c) for f, c, n in _data_sources(msg)
                          if n == "linux.ftrace"]
        if not new_ftrace["enabled"]:
            for field, _config in ftrace_entries:
                msg.fields.remove(field)
        elif not old_ftrace["enabled"]:
            msg.add("data_sources", _default_ftrace(new_ftrace))
        elif ftrace_entries:
            _apply_ftrace(ftrace_entries[0][1], new_ftrace)

    old_sources, new_sources = old["sources"], model["sources"]
    for name, enabled in new_sources.items():
        if name not in old_sources or enabled == old_sources[name]:
            continue
        if enabled:
            msg.add("data_sources", _default_source(name))
        else:
            for field, _config, n in list(_data_sources(msg)):
                if n == name:
                    msg.fields.remove(field)

    return serialize(msg)


def _apply_ftrace(config: Message, model: dict):
    ftrace = config.first("ftrace_config")
    if ftrace is None or not isinstance(ftrace.value, Message):
        ftrace = Field("ftrace_config", Message())
        config.fields.append(ftrace)
    cfg = ftrace.value
    _sync_list(cfg, "ftrace_events", model["events"])
    _sync_list(cfg, "atrace_categories", model["atrace_categories"])
    _sync_list(cfg, "atrace_apps", model["atrace_apps"])
    old_size = cfg.first("buffer_size_kb")
    old_value = (_int(old_size.value)
                 if old_size and not isinstance(old_size.value, Message)
                 else None)
    if model["buffer_size_kb"] != old_value:
        if model["buffer_size_kb"] is None:
            cfg.remove("buffer_size_kb")
        else:
            cfg.set_scalar("buffer_size_kb",
                           str(int(model["buffer_size_kb"])))


def _collect_builtin():
    events = set(_COMMON_FTRACE_EVENTS)
    categories = set()
    preset_dir = paths.resource_dir() / "presets"
    if preset_dir.is_dir():
        for path in sorted(preset_dir.glob("*.pbtx")):
            try:
                model = model_from_text(path.read_text(encoding="utf-8"))
            except (PbtxError, OSError):
                continue
            events.update(model["ftrace"]["events"])
            categories.update(model["ftrace"]["atrace_categories"])
    return sorted(events), sorted(categories)


BUILTIN_FTRACE_EVENTS, BUILTIN_ATRACE_CATEGORIES = _collect_builtin()

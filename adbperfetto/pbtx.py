"""Parser and serializer for the Perfetto TraceConfig text format.

Keeps a tree (Message/Field) that preserves comments so configs can be
edited without losing human annotations. Scalar values are stored as the
raw token text exactly as written (including quotes for strings).
"""

MAX_FLIGHT_LIFETIME_MS = 604800000  # 7 days


class PbtxError(Exception):
    def __init__(self, message: str, line: int, col: int):
        super().__init__(message)
        self.message = message
        self.line = line
        self.col = col

    def __str__(self):
        return f"{self.message} (line {self.line}, col {self.col})"


class Field:
    __slots__ = ("name", "value", "comments")

    def __init__(self, name: str, value, comments: list[str] | None = None):
        self.name = name
        self.value = value  # str (raw token) or Message
        self.comments = comments if comments is not None else []

    def __eq__(self, other):
        return (isinstance(other, Field) and self.name == other.name
                and self.value == other.value and self.comments == other.comments)

    def __repr__(self):
        return f"Field({self.name!r}, {self.value!r})"


class Message:
    __slots__ = ("fields", "trailing_comments")

    def __init__(self, fields: list[Field] | None = None,
                 trailing_comments: list[str] | None = None):
        self.fields = fields if fields is not None else []
        self.trailing_comments = (trailing_comments
                                  if trailing_comments is not None else [])

    def get(self, name: str) -> list[Field]:
        return [f for f in self.fields if f.name == name]

    def first(self, name: str) -> Field | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def remove(self, name: str) -> list[Field]:
        removed = [f for f in self.fields if f.name == name]
        if removed:
            self.fields = [f for f in self.fields if f.name != name]
        return removed

    def set_scalar(self, name: str, raw: str):
        field = self.first(name)
        if field is None:
            self.fields.append(Field(name, raw))
        else:
            field.value = raw

    def add(self, name: str, value, comments: list[str] | None = None):
        self.fields.append(Field(name, value, comments))

    def __eq__(self, other):
        return (isinstance(other, Message) and self.fields == other.fields
                and self.trailing_comments == other.trailing_comments)


# ---------------------------------------------------------------- tokenizer

_DELIMS = set('{}[]:;,\'"#') | set(' \t\r\n')


class _Token:
    __slots__ = ("kind", "text", "line", "col")

    def __init__(self, kind, text, line, col):
        self.kind = kind      # atom | string | comment | punct char
        self.text = text      # raw text (comments: text after '#')
        self.line = line
        self.col = col


def _tokenize(text: str) -> list[_Token]:
    tokens = []
    i, line, col = 0, 1, 1
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\r":
            i += 1
            col += 1
            continue
        if ch == "\n":
            i += 1
            line += 1
            col = 1
            continue
        if ch == "#":
            j = i + 1
            while j < n and text[j] != "\n":
                j += 1
            tokens.append(_Token("comment", text[i + 1:j].rstrip(), line, col))
            col += j - i
            i = j
            continue
        if ch in "{}[]:;,":
            tokens.append(_Token(ch, ch, line, col))
            i += 1
            col += 1
            continue
        if ch in "\"'":
            start, start_col = i, col
            quote = ch
            j = i + 1
            while j < n:
                c = text[j]
                if c == "\\":
                    j += 2
                    continue
                if c == quote:
                    break
                if c == "\n":
                    raise PbtxError("Unterminated string literal", line, start_col)
                j += 1
            if j >= n:
                raise PbtxError("Unterminated string literal", line, start_col)
            tokens.append(_Token("string", text[i:j + 1], line, start_col))
            col += j + 1 - i
            i = j + 1
            continue
        # bare atom: run until a delimiter or whitespace
        j = i
        while j < n and text[j] not in _DELIMS:
            j += 1
        if j == i:
            raise PbtxError(f"Unexpected character {ch!r}", line, col)
        tokens.append(_Token("atom", text[i:j], line, col))
        col += j - i
        i = j
    return tokens


# ------------------------------------------------------------------ parser

def _err(token: _Token, message: str) -> PbtxError:
    return PbtxError(message, token.line, token.col)


def _parse_fields(tokens: list[_Token], pos: int, top: bool):
    fields: list[Field] = []
    pending: list[str] = []
    n = len(tokens)
    while pos < n:
        tok = tokens[pos]
        if tok.kind in ";,":
            pos += 1
            continue
        if tok.kind == "comment":
            pending.append(tok.text)
            pos += 1
            continue
        if tok.kind == "}":
            if top:
                raise _err(tok, "Unexpected '}'")
            return Message(fields, pending), pos + 1
        if tok.kind != "atom":
            raise _err(tok, f"Expected a field name, got {tok.text!r}")
        name = tok.text
        pos += 1
        if pos < n and tokens[pos].kind == ":":
            pos += 1
        if pos >= n:
            raise PbtxError(f"Expected a value for field {name!r}",
                            tok.line, tok.col)
        val = tokens[pos]
        if val.kind == "[":
            pos += 1
            values = []
            while True:
                if pos >= n:
                    raise PbtxError("Unterminated list", val.line, val.col)
                item = tokens[pos]
                if item.kind == "]":
                    pos += 1
                    break
                if item.kind in ("atom", "string"):
                    values.append(item.text)
                elif item.kind != ",":
                    raise _err(item, f"Unexpected {item.text!r} in list")
                pos += 1
            for index, raw in enumerate(values):
                fields.append(Field(name, raw, pending if index == 0 else []))
            pending = []
            continue
        if val.kind == "{":
            sub, pos = _parse_fields(tokens, pos + 1, top=False)
            fields.append(Field(name, sub, pending))
            pending = []
            continue
        if val.kind in ("atom", "string"):
            fields.append(Field(name, val.text, pending))
            pending = []
            pos += 1
            continue
        raise _err(val, f"Expected a value for field {name!r}")
    if not top:
        raise PbtxError("Unexpected end of input inside '{' block",
                        tokens[-1].line if tokens else 1,
                        tokens[-1].col if tokens else 1)
    return Message(fields, pending), pos


def parse(text: str) -> Message:
    tokens = _tokenize(text)
    msg, pos = _parse_fields(tokens, 0, top=True)
    return msg


# -------------------------------------------------------------- serializer

def _lines(msg: Message, indent: int) -> list[str]:
    pad = "    " * indent
    out = []
    for field in msg.fields:
        out.extend(pad + "#" + c for c in field.comments)
        if isinstance(field.value, Message):
            if not field.value.fields and not field.value.trailing_comments:
                out.append(f"{pad}{field.name} {{}}")
            else:
                out.append(f"{pad}{field.name} {{")
                out.extend(_lines(field.value, indent + 1))
                out.append(pad + "}")
        else:
            out.append(f"{pad}{field.name}: {field.value}")
    out.extend(pad + "#" + c for c in msg.trailing_comments)
    return out


def serialize(msg: Message) -> str:
    return "\n".join(_lines(msg, 0)) + "\n"


# -------------------------------------------------------------- transforms

def to_record(text: str, duration_ms: int | None) -> str:
    """Shape a config for detached recording into a file."""
    msg = parse(text)
    msg.set_scalar("write_into_file", "true")
    if msg.first("file_write_period_ms") is None:
        msg.add("file_write_period_ms", "2500")
    if duration_ms is not None:
        msg.set_scalar("duration_ms", str(int(duration_ms)))
    msg.remove("trigger_config")
    return serialize(msg)


def to_flight(text: str, *, session_name: str, trigger_name: str,
              lifetime_ms: int, clone: bool) -> str:
    """Shape a config for a long-running 'flight recorder' session."""
    if not 0 < lifetime_ms < MAX_FLIGHT_LIFETIME_MS:
        raise ValueError(
            f"Flight lifetime must be between 0 and "
            f"{MAX_FLIGHT_LIFETIME_MS} ms (7 days)")
    msg = parse(text)
    for name in ("duration_ms", "write_into_file", "file_write_period_ms",
                 "max_file_size_bytes", "unique_session_name",
                 "trigger_config"):
        msg.remove(name)
    for buf in msg.get("buffers"):
        if isinstance(buf.value, Message):
            buf.value.set_scalar("fill_policy", "RING_BUFFER")
    msg.add("unique_session_name", f'"{session_name}"')
    trigger_config = Message()
    trigger_config.add("trigger_mode",
                       "CLONE_SNAPSHOT" if clone else "STOP_TRACING")
    if not clone:
        trigger_config.add("use_clone_snapshot_if_available", "true")
    trigger_config.add("trigger_timeout_ms", str(int(lifetime_ms)))
    triggers = Message()
    triggers.add("name", f'"{trigger_name}"')
    triggers.add("stop_delay_ms", "2000")
    trigger_config.add("triggers", triggers)
    msg.add("trigger_config", trigger_config)
    return serialize(msg)


def duration_ms(text: str) -> int | None:
    field = parse(text).first("duration_ms")
    if field is None or isinstance(field.value, Message):
        return None
    try:
        return int(field.value)
    except ValueError:
        return None


def total_buffer_kb(text: str) -> int:
    total = 0
    for buf in parse(text).get("buffers"):
        if not isinstance(buf.value, Message):
            continue
        field = buf.value.first("size_kb")
        if field is not None and not isinstance(field.value, Message):
            try:
                total += int(field.value)
            except ValueError:
                pass
    return total

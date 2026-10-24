"""Comment and whitespace normalization that preserves string literals."""


def _walk(source: str, transform_code):
    out: list[str] = []
    i = 0
    n = len(source)
    while i < n:
        if source.startswith("--", i):
            while i < n and source[i] != "\n":
                i += 1
            continue
        if source.startswith("/*", i):
            i += 2
            while i < n and not source.startswith("*/", i):
                if source[i] == "\n":
                    out.append("\n")
                i += 1
            if source.startswith("*/", i):
                i += 2
            continue
        if source[i] == "'":
            literal = ["'"]
            i += 1
            while i < n:
                literal.append(source[i])
                if source[i] == "'" and i + 1 < n and source[i + 1] == "'":
                    literal.append(source[i + 1])
                    i += 2
                    continue
                i += 1
                if literal[-1] == "'":
                    break
            out.append("".join(literal))
            continue
        start = i
        while i < n and source[i] != "'" and not source.startswith("--", i) and not source.startswith("/*", i):
            i += 1
        out.append(transform_code(source[start:i]))
    return "".join(out)


def mask_non_code(source: str) -> str:
    """Replace comments and string literals with spaces. Newlines stay put."""

    out: list[str] = []
    i = 0
    n = len(source)
    while i < n:
        if source.startswith("--", i):
            while i < n and source[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if source.startswith("/*", i):
            out.append("  ")
            i += 2
            while i < n and not source.startswith("*/", i):
                out.append("\n" if source[i] == "\n" else " ")
                i += 1
            if i < n:
                out.append("  ")
                i += 2
            continue
        if source[i] == "'":
            out.append(" ")
            i += 1
            while i < n:
                if source[i] == "'" and i + 1 < n and source[i + 1] == "'":
                    out.append("  ")
                    i += 2
                    continue
                char = source[i]
                i += 1
                out.append("\n" if char == "\n" else " ")
                if char == "'":
                    break
            continue
        out.append(source[i])
        i += 1
    return "".join(out)


def normalize_plsql(source: str) -> str:
    """Collapse comments, code case, and whitespace. String literals stay intact."""

    def fold(chunk: str) -> str:
        return chunk.upper()

    folded = _walk(source, fold)
    return " ".join(folded.split())

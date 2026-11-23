"""CC0 synthetic probe assets. No external media, font outlines or user data."""

from base64 import b64decode
import io
import wave

# FontBuilder: one U+0041 glyph, rectangle (64,64)–(640,640) on a 1000-unit em.
# Generated from four straight lines. The file's licenseDescription also declares CC0.
FONT = (
    "d09GMgABAAAAAAF8AAoAAAAAAzwAAAE0AAEAAAAAAAAAAAAAAAAAAAAAAAAAAAAABmAANAocNgE2AiQDBgsGAAQgBYIjBy0bmQIA"
    "noWx22KIKicXRXQrw0a+xPPfL9257780WrU7jEdYEqEZH4fUWLkS34VB7/yf6ycPpx9kZVUg/bzzwiZ+KFeFkAX87+futZY"
    "kFh2EUS612loQ+W3xaUpt6TMPKMAP9KG6ZTjV51it6hH9InrRqDmttOYC/bIFwIE32KICGfkDZgoLA3ct1ScmQPPgsOTiBEC"
    "BRtBYARqQfqVcL67rAgBByMfWkTP/t3j8wef710Pdq60HYCgQ9jBqnqFCK1JgasYeRGlpSoJtvYEgrgCeysYVrT3i8blrIiy9"
    "ExPHc+XCkae1ZzM49+SXfJerq6++/CByfmuqDKXoAnMQyEwR5TvDnwGJr8WlU7v0Skxid8sdarFa6zbJHStkWQmcAgEZoxD7"
    "n5BZ9F5qh1NugggrUuuzDwA="
)

# FFmpeg lavfi color=c=0x005588:s=8x8:r=5, 0.4 s, two VP8 solid-color frames, no audio.
VIDEO = (
    "GkXfo59ChoEBQveBAULygQRC84EIQoKEd2VibUKHgQJChYECGFOAZwH/////////EU2bdKtNu4tTq4QVSalmU6yBoU27i1Or"
    "hBZUrmtTrIHWTbuMU6uEElTDZ1OsggEj7AEAAAAAAABoAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAVSalmsCrXsYMP"
    "QkBNgIxMYXZmNjMuMS4xMDJXQYxMYXZmNjMuMS4xMDJEiYhAeQAAAAAAABZUrmvIrgEAAAAAAAA/14EBc8WIjc9C5iSp0TCcgQAi"
    "tZyDdW5kiIEAhoVWX1ZQOIOBASPjg4QL68IA4JCwgQi6gQiagQJVsIRVuYEBElTDZ9Zzc59jwIBnyJlFo4dFTkNPREVSRIeM"
    "TGF2ZjYzLjEuMTAyc3OxY8CLY8WIjc9C5iSp0TBnyKBFo4dFTkNPREVSRIeTTGF2YzYzLjEuMTAyIGxpYnZweB9DtnXZ54EA"
    "o72BAACAsAIAnQEqCAAIAABHCIWFiIWEiAICAnWqA/gCDP37AP7+SAr/37S99pe+0v9e3/5rt812+a7f80GAo5WBAMgAsQEABR"
    "CsABgAGFgv9AAIAAA="
)


def media_files():
    wav = io.BytesIO()
    with wave.open(wav, "wb") as stream:
        stream.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\x00\x00" * 640)
    return {
        "static/square.woff2": b64decode(FONT),
        "static/tone.wav": wav.getvalue(),
        "static/level.json": b'{"x":2,"y":4}',
        "static/frames.webm": b64decode(VIDEO),
    }

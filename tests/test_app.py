from app.main import safe_stem


def test_safe_stem_keeps_korean_and_removes_unsafe_chars():
    assert safe_stem("회의 녹음 (최종) #1.mp3") == "회의_녹음_최종_1"


def test_safe_stem_fallback():
    assert safe_stem("###.mp3") == "audio"


def test_safe_stem_limits_length():
    assert len(safe_stem("a" * 120 + ".wav")) == 80

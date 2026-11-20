"""한국어 조사 고르기: 앞 낱말의 마지막 글자 받침에 따라 이/가, 은/는, 을/를, 와/과, 으로/로, 이나/나 를 붙인다.
이름표(온톨로지)를 문장에 끼워 넣는 곳은 조사를 손으로 적지 않고 이 도우미를 쓴다 (이름표가 바뀌어도 문장이 맞는다)."""
from __future__ import annotations

# 받침 있음 / 없음. "으로/로" 는 ㄹ 받침도 "로"
PAIRS = {"이/가": ("이", "가"), "은/는": ("은", "는"), "을/를": ("을", "를"), "와/과": ("과", "와"), "으로/로": ("으로", "로"), "이나/나": ("이나", "나")}
_LATIN_FINAL = {"l": 8, "m": 16, "n": 4, "r": 8}  # 영어 글자의 읽는 소리 끝: L(엘)·M(엠)·N(엔)·R(알) 은 받침이 있고 나머지는 없는 것으로 본다
_DIGIT_FINAL = {"0": 21, "1": 8, "3": 16, "6": 1, "7": 8, "8": 8}  # 영(ㅇ)·일(ㄹ)·삼(ㅁ)·육(ㄱ)·칠(ㄹ)·팔(ㄹ)


def final_consonant(word: str) -> int:
    """마지막 글자의 받침 번호 (0 = 받침 없음, 8 = ㄹ). 한글이 아니면 읽는 소리를 어림한다."""
    text = (word or "").rstrip(" )]」』'\"")
    last = text[-1:] if text else ""
    if "가" <= last <= "힣":
        return (ord(last) - 0xAC00) % 28
    if last.lower() in _LATIN_FINAL and last.isascii():
        return _LATIN_FINAL[last.lower()]
    return _DIGIT_FINAL.get(last, 0)


def josa(word: str, pair: str) -> str:
    """`word` 뒤에 맞는 조사를 붙인 글. `pair` 는 "이/가" 처럼 PAIRS 의 키: josa("영업기회", "이/가") == "영업기회가"."""
    with_final, without = PAIRS[pair]
    final = final_consonant(word)
    if pair == "으로/로":
        return f"{word}{without if final in (0, 8) else with_final}"
    return f"{word}{with_final if final else without}"

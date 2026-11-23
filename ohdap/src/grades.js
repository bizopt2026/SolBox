export const GRADES = ['초3', '초4', '초5', '초6', '중1', '중2', '중3', '고1', '고2', '고3'];

export function isElementary(grade) {
  return typeof grade === 'string' && grade.charAt(0) === '초';
}

export function gradeLabel(grade) {
  return grade === 'auto' || !grade ? '자동' : grade;
}

const WORDS_NORMAL = {
  concepts: '핵심 개념', solutions: '풀이', board: '판서로 풀이 보기', review: '복습',
  drill: '비슷한 문제 풀기', mistakes: '이런 실수 조심', why: '왜 틀렸을까?',
  myAnswer: '내가 쓴 답', answer: '정답', upload: '틀린 문제 올리기',
  wrongMsg: '아쉬워요', rightMsg: '맞았어요!'
};
const WORDS_KID = {
  concepts: '꼭 기억해요', solutions: '이렇게 풀어요', board: '선생님 설명 듣기', review: '다시 풀기',
  drill: '비슷한 문제 도전!', mistakes: '이런 실수 조심', why: '어디서 틀렸을까?',
  myAnswer: '내 답', answer: '정답', upload: '틀린 문제 찍기',
  wrongMsg: '괜찮아요, 같이 다시 봐요', rightMsg: '맞았어요! 별 1개'
};

export function words(grade) {
  return isElementary(grade) ? WORDS_KID : WORDS_NORMAL;
}

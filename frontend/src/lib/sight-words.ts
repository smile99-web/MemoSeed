// Sight words (高频功能词) — MUST stay in sync with the backend
// app/services/memory_scheduler.py SIGHT_WORDS. A pytest guard
// (test_sight_words_frontend_sync.py) parses this file and compares sets.
//
// These words are learned through sentence exposure, not drilled as
// standalone spelling targets (2026-08-03 视觉词退休决议). 2026-08-10:
// the course-learn sentence spelling flow was still forcing the child to
// keyboard-type every sight word in every sentence (and mistake-practice
// them on failure) — ~1000 review events / ~19h per week on the/a/is/...
// In sentence spelling they are now pre-filled as given text; only content
// words are typed.

export const SIGHT_WORDS: ReadonlySet<string> = new Set([
  "a", "about", "after", "all", "also", "am", "an", "and", "any", "are",
  "at", "be", "because", "been", "before", "between", "but", "by", "can",
  "could", "did", "do", "does", "each", "every", "for", "from", "get", "go",
  "had", "has", "have", "he", "her", "here", "him", "his", "how", "i",
  "if", "in", "into", "is", "it", "its", "just", "let", "me", "mine",
  "my", "no", "not", "now", "of", "on", "or", "our", "out", "over",
  "put", "she", "should", "so", "some", "than", "that", "the", "their", "them",
  "then", "there", "these", "they", "this", "those", "to", "too", "under", "up",
  "us", "very", "was", "we", "well", "were", "what", "when", "where", "who",
  "why", "will", "with", "would", "yes", "you", "your",
]);

export function isSightWord(word: string): boolean {
  return SIGHT_WORDS.has(word.trim().toLowerCase());
}

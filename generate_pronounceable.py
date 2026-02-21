#!/usr/bin/env python3
import argparse
import itertools
import sys

from fonetic import count


def estimate_syllables(word):
    """Estimate the number of syllables in a word using vowel groups."""
    vowels = "aeiouy"
    syllable_count = 0
    prev_char_was_vowel = False

    for char in word.lower():
        if char in vowels and not prev_char_was_vowel:
            syllable_count += 1
            prev_char_was_vowel = True
        else:
            prev_char_was_vowel = False

    # Ensure at least 1 syllable
    return max(1, syllable_count)


def is_pronounceable(word):
    """Check if a word is pronounceable using the fonetic library."""
    total, good, bad = count(word)
    if total == 0:
        return False
    return good == total


def generate_pronounceable_words(
    max_candidates, max_match, start_letter, suffix, length, syllables
):
    """Generate pronounceable words starting with `start_letter`, with given `length` and `syllables`, and append `suffix`."""
    letters = "abcdefghijklmnopqrstuvwxyz"
    count = 0
    matches = 0

    if start_letter not in letters:
        print(f"Error: '{start_letter}' is not a valid letter.", file=sys.stderr)
        sys.exit(1)

    # Generate combinations of the specified length starting with `start_letter`
    for combo in itertools.product(start_letter, *[letters] * (length - 1)):
        if count >= max_candidates or matches >= max_match:
            break
        word = "".join(combo)
        if is_pronounceable(word) and estimate_syllables(word) == syllables:
            print(f"{word}{suffix}")
            matches += 1
        count += 1


def main():
    parser = argparse.ArgumentParser(description="Generate pronounceable words.")
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=10000,
        help="Maximum number of combinations to check (default: 10000).",
    )
    parser.add_argument(
        "--max-match",
        type=int,
        default=100,
        help="Maximum number of pronounceable words to print (default: 100).",
    )
    parser.add_argument(
        "--start-letter",
        type=str,
        default="a",
        help="Starting letter for words (default: 'a').",
    )
    parser.add_argument(
        "--suffix",
        type=str,
        default="",
        help="Suffix to append to each pronounceable word (default: '').",
    )
    parser.add_argument(
        "--length",
        type=int,
        default=3,
        help="Length of the words to generate (default: 3).",
    )
    parser.add_argument(
        "--syllables",
        type=int,
        default=1,
        help="Number of syllables in the words (default: 1).",
    )
    args = parser.parse_args()

    generate_pronounceable_words(
        args.max_candidates,
        args.max_match,
        args.start_letter.lower(),
        args.suffix,
        args.length,
        args.syllables,
    )


if __name__ == "__main__":
    main()

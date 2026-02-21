#!/usr/bin/env python3
import argparse
import itertools
import sys
from metaphone import doublemetaphone

def is_pronounceable(word):
    """Check if a word is pronounceable using the Metaphone algorithm."""
    metaphone_code = doublemetaphone(word)[0]
    return len(metaphone_code) >= 2

def generate_pronounceable_words(max_candidates, max_match, start_letter):
    """Generate pronounceable 3-letter words starting with `start_letter`."""
    letters = "abcdefghijklmnopqrstuvwxyz"
    count = 0
    matches = 0

    # Validate start_letter
    if start_letter not in letters:
        print(f"Error: '{start_letter}' is not a valid letter.", file=sys.stderr)
        sys.exit(1)

    # Generate combinations starting with `start_letter`
    for combo in itertools.product(start_letter, letters, letters):
        if count >= max_candidates or matches >= max_match:
            break
        word = "".join(combo)
        if is_pronounceable(word):
            print(word)
            matches += 1
        count += 1

def main():
    parser = argparse.ArgumentParser(description="Generate pronounceable 3-letter words.")
    parser.add_argument("--max-candidates", type=int, default=10000,
                        help="Maximum number of 3-letter combinations to check (default: 10000).")
    parser.add_argument("--max-match", type=int, default=100,
                        help="Maximum number of pronounceable words to print (default: 100).")
    parser.add_argument("--start-letter", type=str, default="a",
                        help="Starting letter for 3-letter combinations (default: 'a').")
    args = parser.parse_args()

    generate_pronounceable_words(args.max_candidates, args.max_match, args.start_letter.lower())

if __name__ == "__main__":
    main()
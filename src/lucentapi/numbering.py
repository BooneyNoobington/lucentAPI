"""Readable collection identifiers in a mixed-radix numbering scheme.

Each segment is a "digit" with its own alphabet, so the sequence number 1000 becomes
`0·Cyan·1·Rho`. Consecutive identifiers differ in the last segment, which makes them hard to
mix up. This is the "mixed radix" scheme of lucentAndShiny's `las.yaml` (its first four
segments), the same as in lucentLIMS.

The API derives the identifier from the collection's auto-increment id, so two clients
registering at the same moment can't get the same one.
"""

from dataclasses import dataclass

COLOURS = ["Red", "Orange", "Yellow", "Green", "Cyan", "Blue", "Violet"]
GREEK = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta", "Iota", "Kappa", "Lambda", "Mu",
         "Nu", "Xi", "Omicron", "Pi", "Rho", "Sigma", "Tau", "Upsilon", "Phi", "Chi", "Psi", "Omega"]
DIGITS = [str(n) for n in range(10)]


@dataclass(frozen=True)
class NumberingScheme:
    segments: tuple[tuple[str, ...], ...]
    separator: str = "·"

    @property
    def capacity(self) -> int:
        result = 1
        for symbols in self.segments:
            result *= len(symbols)
        return result

    def identifier(self, sequence: int) -> str | None:
        if not 0 <= sequence < self.capacity:
            return None
        parts = []
        for symbols in reversed(self.segments):
            sequence, index = divmod(sequence, len(symbols))
            parts.append(symbols[index])
        return self.separator.join(reversed(parts))

    def sequence(self, identifier: str) -> int | None:
        parts = identifier.split(self.separator)
        if len(parts) != len(self.segments):
            return None
        result = 0
        for part, symbols in zip(parts, self.segments):
            if part not in symbols:
                return None
            result = result * len(symbols) + symbols.index(part)
        return result


LUCENT = NumberingScheme((tuple(DIGITS), tuple(COLOURS), tuple(DIGITS), tuple(GREEK)))

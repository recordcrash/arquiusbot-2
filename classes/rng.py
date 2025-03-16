"""
ChainProofRHG – Chain-Proof Random Hit Generator

This module provides a class for more consistent random hit generation,
modeled after the system used in Warcraft III and DOTA 2.

It includes helper functions to convert between a “base” probability
and an “effective” probability. Documentation is incomplete.
"""

from math import ceil, log10
from random import random
from functools import partialmethod
from numbers import Number
import operator
from typing import Any

EPSILON: float = 1e-6


def base_to_mean(base_proc: float) -> float:
    """
    Calculate the effective (mean) probability from a given base hit chance.

    :param base_proc: The base hit chance (between 0 and 1 inclusive).
    :return: The effective probability as a float.
    :raises ValueError: If base_proc is not between 0 and 1.
    """
    if not (0 <= base_proc <= 1):
        raise ValueError('Probability values lie between 0 and 1 inclusive.')
    elif base_proc >= 0.5:
        return 1 / (2 - base_proc)
    hit_chance: float = base_proc
    chance_sum: float = base_proc
    hits_count: float = base_proc
    # Sum the individual pass chances to get the cumulative number of chances.
    for i in range(2, int(ceil(1 / base_proc)) + 1):
        hit_chance = min(1, base_proc * i) * (1 - chance_sum)
        chance_sum += hit_chance
        hits_count += hit_chance * i
    # Take the reciprocal to convert from "1 in N times" to a probability.
    return 1 / hits_count


def mean_to_base(mean_proc: float, epsilon: float = EPSILON) -> float:
    """
    Use the bisection method to find the base hit chance corresponding to a given effective probability.

    :param mean_proc: The effective (mean) probability (between 0 and 1 inclusive).
    :param epsilon: The allowable error for the bisection search.
    :return: The base probability corresponding to the given effective probability.
    :raises ValueError: If mean_proc is not between 0 and 1.
    """
    if not (0 <= mean_proc <= 1):
        raise ValueError('Probability values lie between 0 and 1 inclusive.')
    elif mean_proc >= 2/3:
        return 2 - (1 / mean_proc)
    lower: float = 0.0
    upper: float = mean_proc
    while True:
        midpoint: float = (lower + upper) / 2
        midvalue: float = base_to_mean(midpoint)
        if abs(midvalue - mean_proc) < epsilon:
            break
        elif midvalue < mean_proc:
            lower = midpoint
        else:
            upper = midpoint
    return midpoint


class ChainProofRHG:
    """
    Chain-Proof Random Hit Generator for more consistent RNG.

    Emulates the random hit generation used in Warcraft 3 and DOTA 2.

    :param mean_proc: The effective (mean) hit probability.
    :param epsilon: The precision parameter for internal calculations.
    """
    __slots__ = (
        '_epsilon', '_fail_count', '_last_count',
        '_lock', '_mean_proc', '_base_proc', '_procnow',
    )

    def __init__(self, mean_proc: float, epsilon: float = EPSILON) -> None:
        if epsilon > 1e-4:
            raise ValueError('Expected epsilon value too large')
        self._epsilon: float = epsilon
        self._fail_count: int = 0
        self._last_count: int = 0
        self._lock: bool = False
        self._mean_proc: float = round(mean_proc, self.round_places)
        self._base_proc: float = mean_to_base(mean_proc, epsilon)
        self._procnow: float = self._base_proc

    @classmethod
    def from_base_proc(cls, base_proc: float, epsilon: float = EPSILON) -> "ChainProofRHG":
        rhg = cls(1, epsilon)
        rhg._procnow = rhg._base_proc = base_proc
        rhg._mean_proc = round(base_to_mean(base_proc), rhg.round_places)
        return rhg

    def __getattr__(self, name: str) -> Any:
        if name in ('p', 'mean_proc'):
            return self._mean_proc
        elif name in ('c', 'base_proc'):
            return self._base_proc
        elif name == 'procnow':
            return self._procnow
        elif name == 'epsilon':
            return self._epsilon
        elif name == 'round_places':
            return -int(ceil(log10(self._epsilon)))
        elif name == 'last_count':
            return self._last_count
        elif name == 'max_fails':
            return int(ceil(1 / self._base_proc))
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

    def base_to_mean(self) -> float:
        """Calculate the effective probability from the current base probability."""
        return base_to_mean(self._base_proc)

    def reset(self) -> None:
        """Reset the internal counters."""
        self._fail_count = 0
        self._lock = False

    def test_nhits(self, n: int):
        """Generate a sequence of n hit evaluations."""
        return (bool(self) for _ in range(n))

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self._mean_proc}, {self._epsilon!r})"

    def _cmp_op(self, other: Any, op) -> Any:
        if isinstance(other, ChainProofRHG):
            return op((self._mean_proc, self.round_places),
                      (other._mean_proc, other.round_places))
        elif isinstance(other, Number):
            return op(self._mean_proc, other)
        return NotImplemented

    __eq__ = partialmethod(_cmp_op, op=operator.eq)
    __ne__ = partialmethod(_cmp_op, op=operator.ne)
    __lt__ = partialmethod(_cmp_op, op=operator.lt)
    __le__ = partialmethod(_cmp_op, op=operator.le)
    __gt__ = partialmethod(_cmp_op, op=operator.gt)
    __ge__ = partialmethod(_cmp_op, op=operator.ge)

    def __hash__(self) -> int:
        return hash((self._mean_proc, self._epsilon))

    def __bool__(self) -> bool:
        hit: bool = random() < self._procnow
        if hit:
            self._last_count = self._fail_count + 1
            self._fail_count = 0
        else:
            self._fail_count += 1
        return hit

    def __int__(self) -> int:
        return int(bool(self))

    def __float__(self) -> float:
        return self._mean_proc

    def __iter__(self):
        self._lock = False
        return self

    def __next__(self) -> int:
        if self._lock:
            raise StopIteration
        hit: bool = random() < self._procnow
        if hit:
            self._last_count = self._fail_count + 1
            self._fail_count = 0
            self._procnow = self._base_proc
            self._lock = True
            raise StopIteration
        self._procnow = min(1.0, self._procnow + self._base_proc)
        self._fail_count += 1
        return self._fail_count

    def _math_op(self, other: Any, op) -> "ChainProofRHG":
        if isinstance(other, ChainProofRHG):
            return self.__class__(op(self._mean_proc, other._mean_proc), max(self._epsilon, other._epsilon))
        elif isinstance(other, Number):
            return self.__class__(op(self._mean_proc, other), self._epsilon)
        return NotImplemented

    def _rev_math_op(self, other: Any, op) -> "ChainProofRHG":
        if isinstance(other, Number):
            return self.__class__(op(other, self._mean_proc), self._epsilon)
        return NotImplemented

    __add__ = partialmethod(_math_op, op=operator.add)
    __radd__ = partialmethod(_rev_math_op, op=operator.add)
    __sub__ = partialmethod(_math_op, op=operator.sub)
    __rsub__ = partialmethod(_rev_math_op, op=operator.sub)
    __mul__ = partialmethod(_math_op, op=operator.mul)
    __rmul__ = partialmethod(_rev_math_op, op=operator.mul)
    __truediv__ = partialmethod(_math_op, op=operator.truediv)
    __rtruediv__ = partialmethod(_rev_math_op, op=operator.truediv)
    __pow__ = partialmethod(_math_op, op=operator.pow)
    __rpow__ = partialmethod(_rev_math_op, op=operator.pow)

    def _logic_op(self, other: Any, op) -> "ChainProofRHG":
        if isinstance(other, ChainProofRHG):
            return self.__class__(op(self._mean_proc, other._mean_proc), max(self._epsilon, other._epsilon))
        elif isinstance(other, Number) and 0 <= other <= 1:
            return self.__class__(op(self._mean_proc, other), self._epsilon)
        else:
            raise TypeError("Incompatible operand type between probability and non-probability")

    __and__ = partialmethod(_logic_op, op=operator.mul)
    __rand__ = partialmethod(_logic_op, op=operator.mul)
    __xor__ = partialmethod(_logic_op, op=lambda l, r: l + r - 2 * l * r)
    __rxor__ = partialmethod(_logic_op, op=lambda l, r: l + r - 2 * l * r)
    __or__ = partialmethod(_logic_op, op=lambda l, r: l + r - l * r)
    __ror__ = partialmethod(_logic_op, op=lambda l, r: l + r - l * r)

    def __invert__(self) -> "ChainProofRHG":
        return self.__class__(1 - self._mean_proc, self._epsilon)

    def __round__(self, n: int = 0) -> float:
        return round(self._mean_proc, min(n, self.round_places))


if __name__ == '__main__':
    # Some simple tests.
    cprhg = ChainProofRHG(0.25)
    assert cprhg != ChainProofRHG(0.25, 1e-5)
    print(cprhg)
    assert cprhg == 0.25
    assert abs(cprhg.base_to_mean() - 0.25) < cprhg.epsilon
    print(cprhg.base_to_mean() - 0.25)

    cprhg = ChainProofRHG(0.17)
    print(cprhg)
    assert cprhg.mean_proc == 0.17
    assert cprhg.procnow == cprhg.base_proc
    print(cprhg.procnow)
    print(' '.join(map(str, cprhg)), '|', cprhg.last_count)

    a = ChainProofRHG(0.1)
    assert a == 0.1 == ChainProofRHG(0.1)
    assert 0 < a < 1
    assert 0.1 <= a <= 0.1
    b = ChainProofRHG(0.15)
    print(a + b)
    print((a + b).base_proc)
    assert a + b == 0.25
    assert a + 0.1 == 0.2
    assert 0.1 + a == 0.2
    print(0.1 + a)
    a = a + 0.1
    assert a == ChainProofRHG(0.2)
    assert round(a - b, 2) == 0.05
    assert round(a - 0.05, 2) == 0.15
    assert round(0.05 - float(a), 2) == -0.15
    assert a * 5 == 1.
    assert 5 * a == 1.
    assert a * b == 0.03
    b = a * b
    assert b == ChainProofRHG(0.03)
    b = b / a
    assert b == 0.15
    print(a | b)
    print((a | b).base_proc)
    assert a | b == a + b - (a * b)
    print(a & b)
    print((a & b).base_proc)
    assert a & b == a * b
    print(a ^ b)
    print((a ^ b).base_proc)
    assert a ^ b == a + b - (2 * a * b)
    print(~a)
    print((~a).base_proc)
    assert ~~a == a
    cprhg = ChainProofRHG(0.15)
    print(cprhg)
    hitlist = [len([i for i in cprhg]) + 1 for _ in range(25)]
    print(hitlist)
    print(len(hitlist) / sum(hitlist))
    for prob in range(5, 51, 5):
        print(f'{prob:02}%: {mean_to_base(prob/100):0.6f}')

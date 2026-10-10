"""A reference model of the arithmetic of the MC68881/68882 for the FPU corpus (T54), in exact rational arithmetic.

Nothing here comes from the engine: the expected value of every case is derived from the semantics of the M68881/M68882 User's Manual (and IEEE 754 where the
manual defers to it) with Python integers and Fractions. A value is an `Ext`: the sign, the 15-bit biased exponent and the 64-bit significand with its explicit
integer bit, i.e. what FMOVE.X stores (the word of sign and exponent, a zero word, the 64 bits).

Scope: finite and special operands in the formats B, W, L, S, D and X, the four rounding modes, the three precisions, FPSR condition codes, exception and accrued bits.
What is not modelled is left out of the corpus on purpose (denormal results of single or double precision, both operands a NaN, FMOVE.P): see tests/host/fpu_corpus.py.
"""
from collections import namedtuple
from fractions import Fraction
from decimal import Decimal, getcontext

BIAS, EMAX = 16383, 16383
EMIN = -16383          # the smallest binade: biased exponent 0 with the integer bit set. The 68881 gives exponent 0 no special weight: value = significand * 2**(exp - BIAS - 63)
RN, RZ, RM, RP = 0, 1, 2, 3                       # FPCR bits 5-4
PREC_X, PREC_S, PREC_D = 0, 1, 2                  # FPCR bits 7-6
BITS = {PREC_X: 64, PREC_S: 24, PREC_D: 53}
MODE_NAME = {RN: "rn", RZ: "rz", RM: "rm", RP: "rp"}
PREC_NAME = {PREC_X: "x", PREC_S: "s", PREC_D: "d"}

CC_N, CC_Z, CC_I, CC_NAN = 0x08000000, 0x04000000, 0x02000000, 0x01000000
BSUN, SNAN, OPERR, OVFL, UNFL, DZ, INEX2, INEX1 = 0x8000, 0x4000, 0x2000, 0x1000, 0x0800, 0x0400, 0x0200, 0x0100
AE_IOP, AE_OVFL, AE_UNFL, AE_DZ, AE_INEX = 0x80, 0x40, 0x20, 0x10, 0x08

Ext = namedtuple("Ext", "sign exp man")
# Conventions of the Motorola extended format that this model follows (they are the manual's for the format, and SoftFloat's SOFTFLOAT_68K mode where the manual
# leaves the bits open):
#  * the biased exponent 0 means 2**(-16383), not 2**(-16382): a number below 2**-16383 is a denormal (integer bit clear), and 2**-16383 itself is exponent 0 with the integer bit set;
#  * an infinity is generated with a zero significand, and a NaN converted from a narrower format has no integer bit (the bit is "don't care" in the format);
#  * UNFL is set for every tiny result (below 2**-16383), exact or not, and INEX2 only when rounding changed the value (an exact result beyond the range sets OVFL alone);
#  * an operation with a NaN operand returns that NaN quieted and with its sign unchanged (FNEG and FSUB do not flip it), flagging SNAN for a signalling one;
#  * FSUB and FADD of two infinities of the same exponent return the source operand when the signs make it an addition (SoftFloat addsub_swap_inf of the 6888x);
#  * FCMP sets Z when the operands are equal and N when the destination is smaller, never I; an equal pair of zeros or infinities keeps the sign of the destination.
DEFAULT_NAN = Ext(0, 0x7FFF, 0xFFFFFFFFFFFFFFFF)  # the nonsignaling NaN that an invalid operation produces


def zero(sign=0):
    return Ext(sign, 0, 0)


def inf(sign=0):
    return Ext(sign, 0x7FFF, 0)


def is_nan(x):
    return x.exp == 0x7FFF and (x.man << 1) & (2**64 - 1) != 0


def is_snan(x):
    return is_nan(x) and not x.man & (1 << 62)


def is_inf(x):
    return x.exp == 0x7FFF and (x.man << 1) & (2**64 - 1) == 0


def is_zero(x):
    return x.exp == 0 and x.man == 0


def quiet(x):
    return Ext(x.sign, x.exp, x.man | 1 << 62)


def value(x):
    """The exact value of a finite Ext (a Fraction); denormals have exponent 1 - BIAS and no integer bit."""
    assert x.exp != 0x7FFF
    e = x.exp - BIAS - 63
    v = Fraction(x.man) * (Fraction(2) ** e)
    return -v if x.sign else v


def to_words(x):
    """The three longwords FMOVE.X writes: sign/exponent in the high word, a zero word, then the significand."""
    return (x.sign << 31 | x.exp << 16, x.man >> 32, x.man & 0xFFFFFFFF)


def from_words(hi, mid, lo):
    return Ext(hi >> 31, hi >> 16 & 0x7FFF, mid << 32 | lo)


def _exponent(a):
    """e with 2**e <= a < 2**(e+1) for a positive Fraction."""
    e = a.numerator.bit_length() - a.denominator.bit_length()
    if a < Fraction(2) ** e:
        e -= 1
    elif a >= Fraction(2) ** (e + 1):
        e += 1
    return e


def max_finite(sign):
    return Ext(sign, 0x7FFE, 2**64 - 1)


def round_value(v, sign_if_zero, prec=PREC_X, mode=RN):
    """Rounds the exact value `v` to `prec` bits of significand with the extended exponent range. Returns (Ext, FPSR exception bits).
    UNFL is set for a tiny (below the smallest normalized number, before rounding) and inexact result; OVFL|INEX2 for a result beyond the largest."""
    if v == 0:
        return zero(sign_if_zero), 0
    sign = 1 if v < 0 else 0
    a = abs(v)
    e = _exponent(a)
    p = BITS[prec]
    q = max(e, EMIN) - (p - 1)
    scaled = a / (Fraction(2) ** q)
    m = scaled.numerator // scaled.denominator
    rem = scaled - m
    inexact = rem != 0
    up = (mode == RN and (rem > Fraction(1, 2) or (rem == Fraction(1, 2) and m & 1))) or (mode == RM and sign and inexact) or (mode == RP and not sign and inexact)
    if up:
        m += 1
    result = Fraction(m) * (Fraction(2) ** q)
    flags = INEX2 if inexact else 0
    if e < EMIN:
        flags |= UNFL
    if result >= Fraction(2) ** (EMAX + 1):
        to_inf = mode == RN or (mode == RM and sign) or (mode == RP and not sign)
        return (inf(sign) if to_inf else max_finite(sign)), OVFL | (INEX2 if inexact else 0)
    if result == 0:
        return zero(sign), flags
    e = _exponent(result)
    if e < EMIN + 1:             # exponent field 0: the denormals and the binade of 2**-16383
        return Ext(sign, 0, int(result / (Fraction(2) ** (EMIN - 63)))), flags
    return Ext(sign, e + BIAS, int(result / (Fraction(2) ** (e - 63)))), flags


def exact(v):
    """An Ext for a value that is exactly representable (asserts it)."""
    x, flags = round_value(Fraction(v), 0)
    assert flags == 0 and value(x) == Fraction(v), v
    return x


def cc_of(x):
    """The condition code byte of FPSR for a result."""
    bits = CC_N if x.sign else 0
    if is_nan(x):
        bits |= CC_NAN
    elif is_zero(x):
        bits |= CC_Z
    elif is_inf(x):
        bits |= CC_I
    return bits


def accrued(exceptions):
    """The accrued exception byte that a set of exception bits adds (IOP = BSUN|SNAN|OPERR, OVFL, UNFL only with INEX2, DZ, INEX = INEX1|INEX2|OVFL)."""
    ae = 0
    if exceptions & (BSUN | SNAN | OPERR):
        ae |= AE_IOP
    if exceptions & OVFL:
        ae |= AE_OVFL
    if exceptions & UNFL and exceptions & INEX2:
        ae |= AE_UNFL
    if exceptions & DZ:
        ae |= AE_DZ
    if exceptions & (INEX1 | INEX2 | OVFL):
        ae |= AE_INEX
    return ae


Result = namedtuple("Result", "ext fpsr")


def finish(ext, exceptions, extra_cc=None):
    """FPSR after an operation that started from FPSR = 0: the condition codes of the result, the exception bits and the accrued bits they cause."""
    cc = cc_of(ext) if extra_cc is None else extra_cc
    return Result(ext, cc | exceptions | accrued(exceptions))


def invalid():
    return finish(DEFAULT_NAN, OPERR)


def nan_operands(src, dst):
    """The NaN rule for dyadic operations with exactly one NaN operand (both NaN is not modelled): that NaN, quieted; SNAN is flagged."""
    for x in (src, dst):
        if is_nan(x):
            return finish(quiet(x), SNAN if is_snan(x) else 0)
    return None


def add(dst, src, prec=PREC_X, mode=RN, subtract=False):
    n = nan_operands(src, dst)
    if n:
        return n
    original = src
    if subtract:
        src = Ext(src.sign ^ 1, src.exp, src.man)
    if is_inf(dst) and is_inf(src):
        if dst.sign != src.sign:
            return invalid()
        return finish(original if subtract else dst, 0)       # a subtraction of infinities of opposite signs returns the source operand as it was
    if is_inf(dst) or is_inf(src):
        return finish(dst if is_inf(dst) else src, 0)
    total = value(dst) + value(src)
    if total == 0:
        # an exact zero sum is +0, except that the rounding mode minus gives -0; two zeros of the same sign keep it
        both_zero_same = is_zero(dst) and is_zero(src) and dst.sign == src.sign
        sign = dst.sign if both_zero_same else (1 if mode == RM else 0)
        return finish(zero(sign), 0)
    ext, flags = round_value(total, 0, prec, mode)
    return finish(ext, flags)


def sub(dst, src, prec=PREC_X, mode=RN):
    return add(dst, src, prec, mode, subtract=True)


def mul(dst, src, prec=PREC_X, mode=RN):
    n = nan_operands(src, dst)
    if n:
        return n
    sign = dst.sign ^ src.sign
    if (is_inf(dst) and is_zero(src)) or (is_zero(dst) and is_inf(src)):
        return invalid()
    if is_inf(dst) or is_inf(src):
        return finish(inf(sign), 0)
    if is_zero(dst) or is_zero(src):
        return finish(zero(sign), 0)
    ext, flags = round_value(value(dst) * value(src), sign, prec, mode)
    return finish(ext, flags)


def div(dst, src, prec=PREC_X, mode=RN):
    n = nan_operands(src, dst)
    if n:
        return n
    sign = dst.sign ^ src.sign
    if (is_zero(dst) and is_zero(src)) or (is_inf(dst) and is_inf(src)):
        return invalid()
    if is_inf(dst):
        return finish(inf(sign), 0)                # an infinite dividend is not a division by zero
    if is_zero(src):
        return finish(inf(sign), DZ)
    if is_inf(src) or is_zero(dst):
        return finish(zero(sign), 0)
    ext, flags = round_value(value(dst) / value(src), sign, prec, mode)
    return finish(ext, flags)


def _isqrt_fraction(v, bits):
    """floor(sqrt(v) * 2**bits) for a Fraction v >= 0, and whether it is exact."""
    scaled = v * (1 << (2 * bits))
    n = scaled.numerator // scaled.denominator
    r = _isqrt(n)
    exact_root = r * r == scaled
    return r, exact_root


def _isqrt(n):
    import math
    return math.isqrt(n)


def sqrt(src, prec=PREC_X, mode=RN):
    if is_nan(src):
        return finish(quiet(src), SNAN if is_snan(src) else 0)
    if is_zero(src):
        return finish(src, 0)
    if src.sign:
        return invalid()
    if is_inf(src):
        return finish(src, 0)
    v = value(src)
    # sqrt to far more bits than any precision keeps, with a sticky bit, then rounded like any other value
    m = _exponent(v) // 2
    r, exact_root = _isqrt_fraction(v / (Fraction(2) ** (2 * m)), 140)
    approx = Fraction(r, 1 << 140) * (Fraction(2) ** m)
    if not exact_root:
        approx += Fraction(1, 1 << 141) * (Fraction(2) ** m)          # strictly between two neighbours at 140 bits: the sticky bit decides ties and directed modes
    ext, flags = round_value(approx, 0, prec, mode)
    if exact_root:
        flags &= ~INEX2
    return finish(ext, flags)


def to_integer(x, mode, integer_mode=None):
    """FINT: the value rounded to an integer in the rounding mode (FINTRZ: toward zero). Returns a Result in extended precision."""
    if is_nan(x):
        return finish(quiet(x), SNAN if is_snan(x) else 0)
    if is_inf(x) or is_zero(x):
        return finish(x, 0)
    v = value(x)
    use = mode if integer_mode is None else integer_mode
    f = v.numerator // v.denominator            # floor
    rem = v - f
    if rem == 0:
        return finish(x, 0)
    if use == RZ:
        n = f + 1 if v < 0 else f
    elif use == RM:
        n = f
    elif use == RP:
        n = f + 1
    else:
        n = f + 1 if (rem > Fraction(1, 2) or (rem == Fraction(1, 2) and f & 1)) else f
    ext, _ = round_value(Fraction(n), 1 if v < 0 else 0)
    return finish(ext, INEX2)


def is_denormal(x):
    return x.exp == 0 and x.man != 0 and not x.man >> 63


def neg(x):
    if is_nan(x):
        return finish(quiet(x), SNAN if is_snan(x) else 0)
    result = Ext(x.sign ^ 1, x.exp, x.man)
    return finish(result, UNFL if is_denormal(result) else 0)


def absolute(x):
    if is_nan(x):
        return finish(quiet(x), SNAN if is_snan(x) else 0)
    result = Ext(0, x.exp, x.man)
    return finish(result, UNFL if is_denormal(result) else 0)


def move(x, prec=PREC_X, mode=RN):
    """FMOVE FPm,FPn: the operand rounded to the precision (nothing changes in extended precision)."""
    if is_nan(x):
        return finish(quiet(x), SNAN if is_snan(x) else 0)
    if is_inf(x):
        return finish(x, 0)
    if is_zero(x):
        return finish(x, 0)
    ext, flags = round_value(value(x), x.sign, prec, mode)
    return finish(ext, flags)


def compare(dst, src):
    """FCMP: only the condition codes: Z for equal operands (and N too when an equal pair of zeros or infinities is negative), N when the destination is smaller, NAN for
    unordered operands, never I."""
    if is_nan(dst) or is_nan(src):
        return finish(dst, SNAN if is_snan(dst) or is_snan(src) else 0, extra_cc=CC_NAN)
    equal = (is_inf(dst) and is_inf(src) and dst.sign == src.sign) or (not is_inf(dst) and not is_inf(src) and value(dst) == value(src))
    if equal:
        keeps_sign = is_zero(dst) or is_inf(dst)
        return finish(dst, 0, extra_cc=CC_Z | (CC_N if keeps_sign and dst.sign else 0))
    return finish(dst, 0, extra_cc=CC_N if _less(dst, src) else 0)


def _less(dst, src):
    """dst < src for operands that are not NaNs."""
    if is_inf(dst) and is_inf(src):
        return bool(dst.sign) and not src.sign
    if is_inf(dst):
        return bool(dst.sign)
    if is_inf(src):
        return not src.sign
    return value(dst) < value(src)


def test(x):
    """FTST: the condition codes of the operand (a NaN reaches the register quieted)."""
    return finish(quiet(x) if is_nan(x) else x, SNAN if is_snan(x) else 0)


def remainder(dst, src, kind):
    """FMOD (quotient truncated) and FREM (quotient rounded to nearest even) of finite operands. Returns (Result, quotient low 7 bits, quotient sign)."""
    a, b = value(dst), value(src)
    q = a / b
    f = q.numerator // q.denominator
    if kind == "mod":
        n = f + 1 if q < 0 and q != f else f
    else:
        rem = q - f
        n = f + 1 if (rem > Fraction(1, 2) or (rem == Fraction(1, 2) and f & 1)) else f
    r = a - n * b
    sign = dst.sign
    ext, flags = round_value(r, sign)
    quot_sign = 1 if q < 0 else 0
    fpsr = cc_of(ext) | flags | accrued(flags) | (abs(n) & 0x7F) << 16 | quot_sign << 23
    return Result(ext, fpsr)


def scale(dst, src):
    """FSCALE: dst * 2**trunc(src) for finite operands."""
    k = value(src)
    k = k.numerator // k.denominator if k >= 0 else -((-k).numerator // (-k).denominator)
    if is_zero(dst):
        return finish(dst, 0)
    ext, flags = round_value(value(dst) * (Fraction(2) ** k), dst.sign)
    return finish(ext, flags)


def getexp(x):
    if is_nan(x):
        return finish(quiet(x), SNAN if is_snan(x) else 0)
    if is_inf(x):
        return invalid()
    if is_zero(x):
        return finish(x, 0)
    e = x.exp - BIAS - 63 + (x.man.bit_length() - 1)
    ext, _ = round_value(Fraction(e), 0)
    return finish(ext, 0)


def getman(x):
    if is_nan(x):
        return finish(quiet(x), SNAN if is_snan(x) else 0)
    if is_inf(x):
        return invalid()
    if is_zero(x):
        return finish(x, 0)
    shift = 64 - x.man.bit_length()
    return finish(Ext(x.sign, BIAS, x.man << shift), 0)


# -- conversions ---------------------------------------------------------------------------------------------------------------------------------------------------

def from_integer(n):
    ext, _ = round_value(Fraction(n), 0)
    return ext


def from_single(bits):
    sign, exp, frac = bits >> 31, bits >> 23 & 0xFF, bits & 0x7FFFFF
    if exp == 0xFF:
        return (inf(sign), 0) if frac == 0 else (Ext(sign, 0x7FFF, (frac | 1 << 22) << 40), 0)
    v = Fraction(frac | (0 if exp == 0 else 1 << 23)) * Fraction(2) ** ((exp if exp else 1) - 127 - 23)
    ext, _ = round_value(-v if sign else v, sign)
    return ext, 0


def from_double(bits):
    sign, exp, frac = bits >> 63, bits >> 52 & 0x7FF, bits & (2**52 - 1)
    if exp == 0x7FF:
        return (inf(sign), 0) if frac == 0 else (Ext(sign, 0x7FFF, (frac | 1 << 51) << 11), 0)
    v = Fraction(frac | (0 if exp == 0 else 1 << 52)) * Fraction(2) ** ((exp if exp else 1) - 1023 - 52)
    ext, _ = round_value(-v if sign else v, sign)
    return ext, 0


def to_integer_format(x, size, mode):
    """FMOVE.B/W/L FPn,<ea>: (stored value as unsigned, FPSR exception bits). Out of range stores the largest magnitude of the sign and sets OPERR."""
    bits = {"b": 8, "w": 16, "l": 32}[size]
    lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    if is_nan(x) or is_inf(x):
        return ((hi if not x.sign else lo) & ((1 << bits) - 1)), OPERR | (SNAN if is_snan(x) else 0)
    v = value(x)
    f = v.numerator // v.denominator
    rem = v - f
    if rem == 0:
        n = f
    elif mode == RZ:
        n = f + 1 if v < 0 else f
    elif mode == RM:
        n = f
    elif mode == RP:
        n = f + 1
    else:
        n = f + 1 if (rem > Fraction(1, 2) or (rem == Fraction(1, 2) and f & 1)) else f
    if n < lo or n > hi:
        return ((hi if v > 0 else lo) & ((1 << bits) - 1)), OPERR
    return n & ((1 << bits) - 1), (INEX2 if rem else 0)


def to_single(x, mode):
    """FMOVE.S FPn,<ea>: (bits, FPSR exception bits)."""
    return _to_ieee(x, mode, 8, 23, 127)


def to_double(x, mode):
    return _to_ieee(x, mode, 11, 52, 1023)


def _to_ieee(x, mode, ebits, fbits, bias):
    emax_field = (1 << ebits) - 1
    if is_nan(x):
        return (x.sign << (ebits + fbits) | emax_field << fbits | 1 << (fbits - 1) | (x.man >> (63 - fbits) & ((1 << (fbits - 1)) - 1))), (SNAN if is_snan(x) else 0)
    if is_inf(x):
        return x.sign << (ebits + fbits) | emax_field << fbits, 0
    if is_zero(x):
        return x.sign << (ebits + fbits), 0
    v = value(x)
    sign = 1 if v < 0 else 0
    a = abs(v)
    e = _exponent(a)
    emin = 1 - bias
    q = max(e, emin) - fbits
    scaled = a / (Fraction(2) ** q)
    m = scaled.numerator // scaled.denominator
    rem = scaled - m
    inexact = rem != 0
    up = (mode == RN and (rem > Fraction(1, 2) or (rem == Fraction(1, 2) and m & 1))) or (mode == RM and sign and inexact) or (mode == RP and not sign and inexact)
    m += 1 if up else 0
    flags = INEX2 if inexact else 0
    res = Fraction(m) * Fraction(2) ** q
    if e < emin:
        flags |= UNFL
    if res >= Fraction(2) ** (bias + 1):
        to_inf = mode == RN or (mode == RM and sign) or (mode == RP and not sign)
        top = ((emax_field - 1) << fbits) | ((1 << fbits) - 1)
        return sign << (ebits + fbits) | (emax_field << fbits if to_inf else top), OVFL | (INEX2 if inexact else 0)
    if res == 0:
        return sign << (ebits + fbits), flags
    e2 = _exponent(res)
    if e2 < emin:
        field, frac = 0, int(res / Fraction(2) ** (emin - fbits))
    else:
        field, frac = e2 + bias, int(res / Fraction(2) ** (e2 - fbits)) & ((1 << fbits) - 1)
    return sign << (ebits + fbits) | field << fbits | frac, flags


# -- constants and transcendental functions at high precision -----------------------------------------------------------------------------------------------------

getcontext().prec = 80


def _pi():
    # Machin: pi = 16 atan(1/5) - 4 atan(1/239), series in Decimal
    def atan_inv(n):
        x = Decimal(1) / n
        total, term, k = Decimal(0), x, 0
        while abs(term) > Decimal(10) ** -82:
            total += (term / (2 * k + 1)) * (1 if k % 2 == 0 else -1)
            term *= x * x
            k += 1
        return total
    return 16 * atan_inv(5) - 4 * atan_inv(239)


PI = _pi()


def dec(v):
    return Decimal(v.numerator) / Decimal(v.denominator)


def frac(d):
    return Fraction(d)


def constant(name):
    """The true value of a FMOVECR constant (a Fraction, to far more than 64 bits)."""
    ln10 = Decimal(10).ln()
    ln2 = Decimal(2).ln()
    table = {
        "pi": frac(PI), "log10_2": frac(Decimal(2).log10()), "e": frac(Decimal(1).exp()), "log2_e": frac(1 / ln2), "log10_e": frac(1 / ln10),
        "zero": Fraction(0), "ln2": frac(ln2), "ln10": frac(ln10), "one": Fraction(1),
    }
    if name.startswith("ten_"):
        return Fraction(10) ** int(name[4:])
    return table[name]


def transcendental(name, x):
    """The true value of f(x) for a finite x (a Fraction at about 80 digits) or None when the function is not defined there; the corpus accepts a few units in the last place."""
    d = dec(x)
    if name == "sqrt":
        return frac(d.sqrt())
    if name == "etox":
        return frac(d.exp())
    if name == "etoxm1":
        return frac(d.exp() - 1)
    if name == "twotox":
        return frac((d * Decimal(2).ln()).exp())
    if name == "tentox":
        return frac((d * Decimal(10).ln()).exp())
    if name == "logn":
        return frac(d.ln()) if d > 0 else None
    if name == "lognp1":
        return frac((1 + d).ln()) if d > -1 else None
    if name == "log10":
        return frac(d.log10()) if d > 0 else None
    if name == "log2":
        return frac(d.ln() / Decimal(2).ln()) if d > 0 else None
    if name == "sinh":
        return frac((d.exp() - (-d).exp()) / 2)
    if name == "cosh":
        return frac((d.exp() + (-d).exp()) / 2)
    if name == "tanh":
        return frac((d.exp() - (-d).exp()) / (d.exp() + (-d).exp()))
    if name in ("sin", "cos", "tan"):
        s, c = _sincos(d)
        return frac({"sin": s, "cos": c, "tan": s / c}[name])
    if name == "atan":
        return frac(_atan(d))
    if name == "asin":
        return frac(_atan(d / (1 - d * d).sqrt())) if abs(d) < 1 else (frac(PI / 2) * (1 if d > 0 else -1) if abs(d) == 1 else None)
    if name == "acos":
        return frac(PI / 2 - _atan(d / (1 - d * d).sqrt())) if abs(d) < 1 else (Fraction(0) if d == 1 else frac(PI) if d == -1 else None)
    if name == "atanh":
        return frac(((1 + d) / (1 - d)).ln() / 2) if abs(d) < 1 else None
    raise KeyError(name)


def _sincos(d):
    two_pi = 2 * PI
    k = (d / two_pi).to_integral_value()
    r = d - k * two_pi
    term_s, term_c = r, Decimal(1)
    s, c = r, Decimal(1)
    for n in range(1, 120):
        term_c = -term_c * r * r / ((2 * n - 1) * (2 * n))
        term_s = -term_s * r * r / ((2 * n) * (2 * n + 1))
        c += term_c
        s += term_s
    return s, c


def _atan(d):
    reductions = 0
    while abs(d) > Decimal("0.2"):
        d = d / (1 + (1 + d * d).sqrt())
        reductions += 1
    total, term, n = Decimal(0), d, 0
    while abs(term) > Decimal(10) ** -78:
        total += term / (2 * n + 1) * (1 if n % 2 == 0 else -1)
        term *= d * d
        n += 1
    return total * (2 ** reductions)


def ulps(x, true_value):
    """How many units in the last place of the 64-bit significand `x` is from `true_value` (a Fraction)."""
    if is_zero(x):
        return abs(true_value) / (Fraction(2) ** (EMIN - 63))
    e = x.exp - BIAS - 63
    return abs(value(x) - true_value) / (Fraction(2) ** e)

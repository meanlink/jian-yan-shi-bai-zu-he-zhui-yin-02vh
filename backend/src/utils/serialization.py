import hashlib
import math


def _canonical(value):
    """与前端 utils/hash.js 的 canonicalJSON 逐字节对齐的规范化序列化。

    规则：对象键按 UTF-16 码元排序（与 JS Array.prototype.sort 一致）、
    紧凑分隔符、不转义非 ASCII；数字按 JS Number.prototype.toString 口径输出
    （整数不带 .0，浮点最短表示），保证浏览器与服务端 SHA256 一致。
    """
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, str):
        return _json_string(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError('存在非有限数值，无法计算指纹')
        return _js_number(value)
    if isinstance(value, (list, tuple)):
        return '[' + ','.join(_canonical(v) for v in value) + ']'
    if isinstance(value, dict):
        keys = sorted(value.keys(), key=lambda k: [ord(ch) for ch in str(k)])
        return '{' + ','.join(_json_string(str(k)) + ':' + _canonical(value[k]) for k in keys) + '}'
    raise TypeError(f'不支持的数据类型：{type(value)!r}')


def _json_string(text: str) -> str:
    out = ['"']
    for ch in text:
        code = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == '\\':
            out.append('\\\\')
        elif ch == '\b':
            out.append('\\b')
        elif ch == '\f':
            out.append('\\f')
        elif ch == '\n':
            out.append('\\n')
        elif ch == '\r':
            out.append('\\r')
        elif ch == '\t':
            out.append('\\t')
        elif code < 0x20:
            out.append('\\u%04x' % code)
        else:
            out.append(ch)  # 与 JS 一致，不转义非 ASCII 与 Unicode
    out.append('"')
    return ''.join(out)


def _fixed_shortest(value: float) -> str:
    """在定点区间内输出能往返还原该 float 的最短小数字符串（JS Number#toString 口径）。"""
    sign = '-' if value < 0 else ''
    text = repr(abs(value))
    if 'e' not in text and 'E' not in text:
        return sign + text
    mantissa, exp = text.replace('E', 'e').split('e')
    digits = mantissa.replace('.', '')
    exponent = int(exp)
    point = exponent + 1  # 小数点前的位数（digits 首位对应 10^exponent）
    if point <= 0:
        return sign + '0.' + '0' * (-point) + digits
    if point >= len(digits):
        return sign + digits + '0' * (point - len(digits))
    return sign + digits[:point] + '.' + digits[point:]


def _js_number(value: float) -> str:
    # 与 JS String(Number) 一致：-0 输出 "0"
    if value == 0:
        return '0'
    abs_value = abs(value)
    # JS Number.prototype.toString：整数且 |x| < 1e21 用定点；其余按最短表示
    if value.is_integer() and abs_value < 1e21:
        return str(int(value))
    negative = value < 0
    # 科学计数法阈值与 JS 一致：|x| >= 1e21 或 |x| < 1e-6
    if abs_value >= 1e21 or abs_value < 1e-6:
        if 'e' in repr(abs_value).lower():
            base, exp = repr(abs_value).lower().split('e')
            exponent = int(exp)
        else:
            exponent = int(math.floor(math.log10(abs_value)))
            base = repr(abs_value / 10 ** exponent).lower().split('e')[0]
        sign = '-' if exponent < 0 else '+'
        return ('-' if negative else '') + f'{base}e{sign}{abs(exponent)}'
    # 定点区间（含 1e-6 ~ 1e-4，Python repr 会误用指数，这里强制 JS 风格定点最短表示）
    return _fixed_shortest(value)


def checksum(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def dataset_dict(d):
    return {'id': d.id, 'name': d.name, 'source': d.source, 'rows': len(d.records), 'checksum': d.checksum, 'created_at': d.created_at}
def job_dict(j):
    return {k:getattr(j,k) for k in ['id','name','dataset_id','params','status','result','error','approved','created_at','finished_at']}
def report_dict(r):
    return {k:getattr(r,k) for k in ['id','name','job_id','destination','interval_seconds','next_run','enabled']}
def delivery_dict(d):
    return {k:getattr(d,k) for k in ['id','report_id','created_at','checksum','filename','destination']}

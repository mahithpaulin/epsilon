"""Epsilon v1 — symbolic coding engine. Python + JavaScript, syntax-verified."""
from .parser import parse
from .ir import validate_module
from . import pyemit, jsemit, verify, ranker

__version__ = '1.0.0'
RANKER_PARAMS = ranker.PARAMS  # 21


def generate(spec, lang='python', rank=True):
    lang = (lang or 'python').lower()
    if lang not in ('python', 'js', 'javascript'):
        raise ValueError("unsupported lang %r: expected python|js" % lang)
    if lang == 'javascript':
        lang = 'js'
    if not (spec or '').strip():
        raise ValueError('empty spec')
    parsed = parse(spec)
    ir = parsed['ir']
    errs = validate_module(ir)
    if errs:
        raise ValueError('IR invalid: %s' % errs[:3])
    if lang == 'python':
        code = pyemit.synthesize(ir)
        verdict = verify.verify_python(code)
    else:
        code = jsemit.synthesize(ir)
        verdict = verify.verify_js(code)
    # ranker is a re-ranker only; single candidate in v1 so it acts as scorer.
    # Kept for API honesty: score reported, output unchanged (no alternative
    # candidates to choose from in v1 deterministic synthesis).
    rscore = None
    if rank:
        try:
            ranker.ensure_trained()
            rscore = ranker.score(ranker.extract(code))
        except Exception:
            rscore = None
    return {'code': code, 'ir': ir, 'verdict': verdict,
            'meta': parsed.get('meta', {}), 'rank_score': rscore,
            'ranker_params': RANKER_PARAMS}


def verify_code(code, lang='python'):
    lang = (lang or 'python').lower()
    if lang in ('js', 'javascript'):
        return verify.verify_js(code)
    return verify.verify_python(code)

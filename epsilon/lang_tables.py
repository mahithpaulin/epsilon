"""Epsilon 2.5 language tables. 25 table-driven languages + 3 full backends.

Each LangSpec is DATA (no logic): the generic renderer in gen_table.py does
the work. `gaps` documents what the table renderer refuses per language
(render raises ValueError naming the node kind). Verification honesty lives
in `check` (tool + argv template) or None (=> UNKNOWN, never claimed).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LangSpec:
    name: str            # registry key
    display: str
    ext: str
    comment: str         # line comment prefix
    tier: str            # 'general' | 'legacy'
    types: dict = field(default_factory=dict)   # int float str bool list dict none any
    func: str = ''       # templates use {name} {params} {ret}
    param: str = ''      # template uses {name} {type}
    var: str = ''        # {name} {type} {val}
    const: str = ''      # const variant ('' = same as var)
    block_open: str = '{'
    block_close: str = '}'
    if_: str = 'if ({cond}) {'
    elif_: str = '} else if ({cond}) {'
    else_: str = '} else {'
    while_: str = 'while ({cond}) {'
    for_style: str = 'c'      # c | in | pascal | basic | do | perform | each | none
    for_in: str = ''          # template {var} {iter} for in/each styles
    ret: str = 'return {expr};'
    true: str = 'true'
    false: str = 'false'
    null: str = 'null'
    eq: str = '=='
    ne: str = '!='
    str_eq: str = ''          # '' = use eq/ne; else '{l} <op> {r}' template
    str_ne: str = ''
    not_: str = '!'
    and_: str = '&&'
    or_: str = '||'
    list_open: str = '['
    list_close: str = ']'
    list_sep: str = ', '
    dict_open: str = ''       # '' = unsupported
    dict_close: str = ''
    print_: str = ''          # template {args}
    stmt_end: str = ';'
    class_style: str = 'full'  # full | struct | none
    try_: str = ''             # '' = unsupported
    interp: str = ''           # interpolation note for docs
    sigil: str = ''            # variable prefix ($, etc.)
    assign: str = '='          # assignment operator (R uses <-)
    check: tuple = ()          # (tool, argv-template...) or () = UNKNOWN
    gaps: tuple = ()


def _t(**kw):
    base = dict(int='int', float='float', str='str', bool='bool',
                list='list', dict='dict', none='None', any='Any')
    base.update(kw)
    return base


GO = LangSpec(
    name='go', display='Go', ext='.go', comment='//', tier='general',
    types=_t(int='int', float='float64', str='string', bool='bool',
             list='[]any', dict='map[string]any', none='nil', any='any'),
    func='func {name}({params}){ret} {', param='{name} {type}',
    var='{name} := {val}', const='{name} := {val}',
    print_='fmt.Println({args})', class_style='struct', try_='',
    check=(), gaps=('classes(full)->struct+funcs', 'exceptions->panic',
                    'string-interpolation', 'generics'),
)
RUST = LangSpec(
    name='rust', display='Rust', ext='.rs', comment='//', tier='general',
    types=_t(int='i32', float='f64', str='String', bool='bool',
             list='Vec<String>', dict='HashMap<String,String>', none='None',
             any='String'),
    func='fn {name}({params}){ret} {', param='{name}: {type}',
    var='let mut {name} = {val};', const='let {name} = {val};',
    for_style='in', for_in='for {var} in {iter} {',
    print_='println!("{}", {args})', class_style='struct', try_='',
    check=(), gaps=('classes(full)->struct+impl', 'null->Option',
                    'exceptions->Result', 'dict-literal'),
)
JAVA = LangSpec(
    name='java', display='Java', ext='.java', comment='//', tier='general',
    types=_t(int='int', float='double', str='String', bool='boolean',
             list='List<Object>', dict='Map<String,Object>', none='null',
             any='Object'),
    func='{ret} {name}({params}) {', param='{type} {name}',
    var='{type} {name} = {val};', const='final {type} {name} = {val};',
    print_='System.out.println({args})', class_style='full',
    try_='try {', str_eq='{l}.equals({r})', str_ne='!{l}.equals({r})',
    check=(), gaps=('top-level-functions->wrapped-in-class',
                    'string-== (lowered to .equals)', 'unsigned'),
)
C = LangSpec(
    name='c', display='C', ext='.c', comment='//', tier='general',
    types=_t(int='int', float='double', str='char*', bool='bool',
             list='int*', dict='void*', none='NULL', any='void*'),
    func='{ret} {name}({params}) {', param='{type} {name}',
    var='{type} {name} = {val};', const='const {type} {name} = {val};',
    print_='printf("%d\\n", {args})', class_style='struct', try_='',
    eq='==', ne='!=', str_eq='strcmp({l}, {r}) == 0',
    str_ne='strcmp({l}, {r}) != 0',
    check=(), gaps=('classes(full)->struct+funcs', 'exceptions',
                    'bool-needs-stdbool', 'string-== (lowered to strcmp)',
                    'dict-literal', 'generics'),
)
CPP = LangSpec(
    name='cpp', display='C++', ext='.cpp', comment='//', tier='general',
    types=_t(int='int', float='double', str='std::string', bool='bool',
             list='std::vector<int>', dict='std::map<std::string,int>',
             none='nullptr', any='auto'),
    func='{ret} {name}({params}) {', param='{type} {name}',
    var='{type} {name} = {val};', const='const {type} {name} = {val};',
    print_='std::cout << {args} << std::endl', class_style='full',
    try_='try {', check=(),
    gaps=('dict-literal',),
)
CSHARP = LangSpec(
    name='csharp', display='C#', ext='.cs', comment='//', tier='general',
    types=_t(int='int', float='double', str='string', bool='bool',
             list='List<int>', dict='Dictionary<string,int>', none='null',
             any='object'),
    func='{ret} {name}({params}) {', param='{type} {name}',
    var='var {name} = {val};', const='var {name} = {val};',
    print_='Console.WriteLine({args})', class_style='full', try_='try {',
    interp='$"{x}"', check=(), gaps=(),
)
RUBY = LangSpec(
    name='ruby', display='Ruby', ext='.rb', comment='#', tier='general',
    types=_t(int='', float='', str='', bool='', list='', dict='',
             none='nil', any=''),
    func='def {name}({params})', param='{name}', var='{name} = {val}',
    block_open='', block_close='end', if_='if {cond}',
    elif_='elsif {cond}', else_='else', while_='while {cond}',
    for_style='in', for_in='for {var} in {iter}',
    ret='return {expr}', print_='puts {args}', class_style='full',
    try_='begin', interp='"#{x}"', stmt_end='',
    check=('ruby', '-c', '{file}'), gaps=('static-types', 'c-style-for'),
)
PHP = LangSpec(
    name='php', display='PHP', ext='.php', comment='//', tier='general',
    types=_t(int='int', float='float', str='string', bool='bool',
             list='array', dict='array', none='null', any='mixed'),
    func='function {name}({params}){ret} {', param='{type} {name}',
    var='${name} = {val};', sigil='$',
    print_='echo {args};', class_style='full', try_='try {',
    interp='"$x"', check=('php', '-l', '{file}'), gaps=(),
)
SWIFT = LangSpec(
    name='swift', display='Swift', ext='.swift', comment='//', tier='general',
    types=_t(int='Int', float='Double', str='String', bool='Bool',
             list='[Any]', dict='[String: Any]', none='nil', any='Any'),
    func='func {name}({params}){ret} {', param='{name}: {type}',
    var='var {name} = {val}', const='let {name} = {val}',
    for_style='in', for_in='for {var} in {iter} {',
    print_='print({args})', class_style='full', try_='do {',
    interp='"\\({x})"', stmt_end='', check=(), gaps=('c-style-for',),
)
KOTLIN = LangSpec(
    name='kotlin', display='Kotlin', ext='.kt', comment='//', tier='general',
    types=_t(int='Int', float='Double', str='String', bool='Boolean',
             list='List<Any>', dict='Map<String,Any>', none='null',
             any='Any'),
    func='fun {name}({params}){ret} {', param='{name}: {type}',
    var='var {name} = {val}', const='val {name} = {val}',
    for_style='in', for_in='for ({var} in {iter}) {',
    print_='println({args})', class_style='full', try_='try {',
    interp='"$x"', stmt_end='', check=(), gaps=('c-style-for',),
)
DART = LangSpec(
    name='dart', display='Dart', ext='.dart', comment='//', tier='general',
    types=_t(int='int', float='double', str='String', bool='bool',
             list='List', dict='Map', none='null', any='dynamic'),
    func='{ret} {name}({params}) {', param='{type} {name}',
    var='var {name} = {val};', const='final {name} = {val};',
    print_='print({args})', class_style='full', try_='try {',
    interp='"$x"', check=(), gaps=(),
)
LUA = LangSpec(
    name='lua', display='Lua', ext='.lua', comment='--', tier='general',
    types=_t(int='', float='', str='', bool='', list='', dict='',
             none='nil', any=''),
    func='function {name}({params})', param='{name}',
    var='local {name} = {val}', block_open='', block_close='end',
    if_='if {cond} then', elif_='elseif {cond} then', else_='else',
    while_='while {cond} do', for_style='basic',
    ret='return {expr}', print_='print({args})', class_style='struct',
    try_='', eq='==', ne='~=',
    check=(), gaps=('static-types', 'classes(full)->tables',
                    'exceptions->pcall', '1-based-index-note'),
)
PERL = LangSpec(
    name='perl', display='Perl', ext='.pl', comment='#', tier='general',
    types=_t(int='', float='', str='', bool='', list='', dict='',
             none='undef', any=''),
    func='sub {name} {', param='{name}', var='my ${name} = {val};',
    sigil='$', print_='print {args};', class_style='struct', try_='',
    eq='==', ne='!=', str_eq='{l} eq {r}', str_ne='{l} ne {r}',
    check=('perl', '-c', '{file}'),
    gaps=('static-types', 'exceptions->eval', 'classes(full)->bless',
          'string-vs-number-eq (heuristic by literal)'),
)
R = LangSpec(
    name='r', display='R', ext='.R', comment='#', tier='general',
    types=_t(int='', float='', str='', bool='', list='', dict='',
             none='NULL', any=''),
    func='{name} <- function({params}) {', param='{name}',
    var='{name} <- {val}', assign='<-', true='TRUE', false='FALSE',
    for_style='in', for_in='for ({var} in {iter}) {',
    ret='return({expr})', print_='print({args})', class_style='struct',
    try_='', stmt_end='',
    check=(), gaps=('static-types', 'classes(full)->S3-list',
                    'precise-null-semantics'),
)
JULIA = LangSpec(
    name='julia', display='Julia', ext='.jl', comment='#', tier='general',
    types=_t(int='Int', float='Float64', str='String', bool='Bool',
             list='Vector', dict='Dict', none='nothing', any='Any'),
    func='function {name}({params}){ret}', param='{name}::{type}',
    var='{name} = {val}', block_open='', block_close='end',
    if_='if {cond}', elif_='elseif {cond}', else_='else',
    while_='while {cond}', for_style='in', for_in='for {var} in {iter}',
    ret='return {expr}', print_='println({args})', class_style='struct',
    try_='try', interp='"$x"', stmt_end='',
    check=(), gaps=('classes(full)->struct',),
)
SCALA = LangSpec(
    name='scala', display='Scala', ext='.scala', comment='//', tier='general',
    types=_t(int='Int', float='Double', str='String', bool='Boolean',
             list='List[Any]', dict='Map[String,Any]', none='null',
             any='Any'),
    func='def {name}({params}){ret} = {', param='{name}: {type}',
    var='var {name} = {val}', const='val {name} = {val}',
    for_style='in', for_in='for ({var} <- {iter}) {',
    print_='println({args})', class_style='full', try_='try {',
    interp='s"$x"', stmt_end='', check=(), gaps=('c-style-for',),
)
HASKELL = LangSpec(
    name='haskell', display='Haskell', ext='.hs', comment='--', tier='general',
    types=_t(int='Int', float='Double', str='String', bool='Bool',
             list='[Int]', dict='Map.Map String Int', none='Nothing',
             any='a'),
    func='{name} :: {sig}', param='{name}', var='{name} = {val}',
    block_open='', block_close='', ret='{expr}', true='True',
    false='False', eq='==', ne='/=', print_='print {args}',
    class_style='none', try_='', stmt_end='',
    check=(), gaps=('statements->expression-position-only',
                    'mutation(assign/augassign/loops)->recursion-needed',
                    'classes->data-decl-only', 'while->error',
                    'for->map/fold-idiom-needed'),
)
FORTRAN = LangSpec(
    name='fortran', display='Fortran', ext='.f90', comment='!', tier='legacy',
    types=_t(int='integer', float='real', str='character(len=*)',
             bool='logical', list='integer, dimension(:)',
             dict='character(len=*)', none='', any=''),
    func='{ret} function {name}({params})', param='{type} :: {name}',
    var='{type} :: {name} = {val}', block_open='', block_close='end',
    if_='if ({cond}) then', elif_='else if ({cond}) then', else_='else',
    while_='do while ({cond})', for_style='do',
    ret='return', true='.true.', false='.false.', eq='==', ne='/=',
    print_='print *, {args}', class_style='struct', try_='', stmt_end='',
    check=(), gaps=('classes(full)->derived-types', 'exceptions',
                    'dynamic-strings', 'dict-literal'),
)
COBOL = LangSpec(
    name='cobol', display='COBOL', ext='.cbl', comment='*>', tier='legacy',
    types=_t(int='PIC 9', float='PIC 9V9', str='PIC X', bool='PIC X',
             list='', dict='', none='', any='PIC X'),
    func='{name} SECTION.', param='{name}', var='01 {name} {type}.',
    block_open='', block_close='', if_='IF {cond}',
    elif_='ELSE IF {cond}', else_='ELSE', while_='PERFORM UNTIL {cond}',
    for_style='perform', ret='EXIT SECTION.',
    true="'T'", false="'F'", eq='=', ne='NOT =',
    print_='DISPLAY {args}.', class_style='none', try_='', stmt_end='.',
    check=(), gaps=('subset-only: vars/if/perform/display/compute',
                    'no-list/dict/class/try/return-values->RETURNING'),
)
ADA = LangSpec(
    name='ada', display='Ada', ext='.adb', comment='--', tier='legacy',
    types=_t(int='Integer', float='Float', str='String', bool='Boolean',
             list='', dict='', none='null', any=''),
    func='function {name}({params}) return {ret} is',
    param='{name} : {type}', var='{name} : {type} := {val};',
    assign=':=', block_open='', block_close='',
    if_='if {cond} then', elif_='elsif {cond} then', else_='else',
    while_='while {cond} loop', for_style='in',
    for_in='for {var} in {iter} loop', ret='return {expr};',
    true='True', false='False', eq='=', ne='/=',
    print_='Put_Line({args});', class_style='struct', try_='',
    stmt_end=';',
    check=(), gaps=('classes(full)->tagged-types', 'exceptions->raise-nodes',
                    'dict-literal'),
)
MATLAB = LangSpec(
    name='matlab', display='MATLAB', ext='.m', comment='%', tier='legacy',
    types=_t(int='', float='', str='', bool='', list='', dict='',
             none='[]', any=''),
    func='function {ret} = {name}({params})', param='{name}',
    var='{name} = {val};', block_open='', block_close='end',
    if_='if {cond}', elif_='elseif {cond}', else_='else',
    while_='while {cond}', for_style='in', for_in='for {var} = {iter}',
    ret='return', true='true', false='false', eq='==', ne='~=',
    print_='disp({args})', class_style='struct', try_='try',
    check=(), gaps=('static-types', 'classes(full)->classdef',
                    '1-based-index-note'),
)
VB = LangSpec(
    name='vb', display='Visual Basic', ext='.vb', comment="'", tier='legacy',
    types=_t(int='Integer', float='Double', str='String', bool='Boolean',
             list='', dict='', none='Nothing', any='Object'),
    func='Function {name}({params}) As {ret}',
    param='{name} As {type}', var='Dim {name} As {type} = {val}',
    block_open='', block_close='End Function', if_='If {cond} Then',
    elif_='ElseIf {cond} Then', else_='Else', while_='While {cond}',
    for_style='basic', ret='Return {expr}', true='True', false='False',
    eq='=', ne='<>', print_='Console.WriteLine({args})',
    class_style='full', try_='Try', check=(),
    gaps=('end-keyword-per-construct (renderer uses End If/While/For)',
          'dict-literal'),
)
OBJC = LangSpec(
    name='objc', display='Objective-C', ext='.m', comment='//',
    tier='general',
    types=_t(int='int', float='double', str='NSString*', bool='BOOL',
             list='NSArray*', dict='NSDictionary*', none='nil', any='id'),
    func='{ret} {name}({params}) {', param='{type} {name}',
    var='{type} {name} = {val};', const='{type} {name} = {val};',
    print_='NSLog(@\"%@\", {args})', class_style='struct', try_='',
    true='YES', false='NO', check=(),
    gaps=('objc-message-sends (c-subset only)', 'classes(full)->struct',
          'exceptions-rare'),
)
GROOVY = LangSpec(
    name='groovy', display='Groovy', ext='.groovy', comment='//',
    tier='general',
    types=_t(int='int', float='double', str='String', bool='boolean',
             list='List', dict='Map', none='null', any='def'),
    func='def {name}({params}) {', param='{name}',
    var='def {name} = {val}', for_style='in',
    for_in='for ({var} in {iter}) {', print_='println {args}',
    class_style='full', try_='try {', interp='"$x"', stmt_end='',
    check=(), gaps=('static-types-optional',),
)
ELIXIR = LangSpec(
    name='elixir', display='Elixir', ext='.ex', comment='#', tier='general',
    types=_t(int='', float='', str='', bool='', list='', dict='',
             none='nil', any=''),
    func='def {name}({params}) do', param='{name}', var='{name} = {val}',
    block_open='', block_close='end', if_='if {cond} do', elif_='',
    else_='else', while_='', for_style='in', for_in='for {var} <- {iter} do',
    ret='{expr}', print_='IO.puts({args})', class_style='struct',
    try_='try do', interp='"#{x}"', stmt_end='',
    check=(), gaps=('mutation (rebinding-only)', 'while->recursion-needed',
                    'return-keyword (implicit)', 'elif->nested-if',
                    'classes(full)->modules+structs'),
)

ALL = [GO, RUST, JAVA, C, CPP, CSHARP, RUBY, PHP, SWIFT, KOTLIN, DART, LUA,
       PERL, R, JULIA, SCALA, HASKELL, FORTRAN, COBOL, ADA, MATLAB, VB, OBJC,
       GROOVY, ELIXIR]

BY_NAME = {s.name: s for s in ALL}

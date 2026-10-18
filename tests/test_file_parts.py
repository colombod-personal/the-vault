"""A collection file sent as text in ordered parts (vault-0n9.3): the pure parts, without a database.

The checksum is CRC-32 over the UTF-16 code units of the whole text (two bytes each, low byte first). The vectors below were computed
with an independent bitwise implementation (not ``zlib``) and checked with Node's ``zlib.crc32``, and the script a coding assistant runs
(``file_parts.recipe()``) is executed with Node, against a stand-in for the assistant's shell, and against the real PowerShell where
there is one (the owner's PC).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap

import pytest

from vault import file_parts
from vault.file_parts import Mismatch, assemble, checksum, crc_of, line_count, ranges

NODE = shutil.which("node")
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


def slow_crc32_of_utf16(text: str) -> int:
    """CRC-32 (reflected, polynomial 0xEDB88320, initial and final xor 0xFFFFFFFF) over the code units of ``text``: bitwise, no table,
    no zlib. A lone surrogate is one unit, as in JavaScript."""
    crc = 0xFFFFFFFF
    for byte in text.encode("utf-16-le", "surrogatepass"):
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xEDB88320 if crc & 1 else crc >> 1
    return crc ^ 0xFFFFFFFF


# The vectors: empty, ASCII, CRLF, a byte-order mark, accents, a character outside the Basic Multilingual Plane, half of a pair. The
# values were computed with slow_crc32_of_utf16 and checked with Node's zlib.crc32 over the same UTF-16 bytes: a change of the
# algorithm must change these on purpose.
VECTORS = [
    ("", 0x0),
    ("123456789", 0xA290E877),
    ("Card Name,Set\r\nSol Ring,C21\r\n", 0xDFBC8D7B),
    ("\ufeffFolder Name,Quantity\r\n", 0x76CAEDC6),
    ("Jötun Grunt, Lim-Dûl, Æther Vial — é\n", 0xC747D451),
    ("Emoji \U0001F600 in a note\n", 0xA2DC0D5E),
    ("half \ud83d", 0x70955702),
]


def test_the_checksum_is_the_crc32_of_the_utf16_code_units_for_every_kind_of_text():
    for text, expected in VECTORS:
        assert checksum(text) == slow_crc32_of_utf16(text) == expected, repr(text)
    # a text and the same text cut anywhere give the same value when continued in order
    whole = "Jötun \U0001F600 Grunt\r\nSol Ring\r\n"
    for cut in range(len(whole) + 1):
        assert crc_of(whole[cut:], crc_of(whole[:cut])) == checksum(whole), cut
    assert checksum("\ufeffa\r\n") != checksum("a\r\n") and checksum("a\r\n") != checksum("a\n")  # BOM and CRLF are part of the text


def test_the_line_count_is_the_number_of_line_feeds():
    assert [line_count(t) for t in ("", "a", "a\n", "a\r\nb\r\n", "a\nb")] == [0, 0, 1, 2, 1]


def test_part_numbers_are_named_in_words():
    assert ranges([3]) == "part 3"
    assert ranges([7, 3]) == "parts 3 and 7"
    assert ranges([3, 4]) == "parts 3 and 4"
    assert ranges([3, 4, 5, 6, 7, 8, 9, 12, 15]) == "parts 3 to 9, 12 and 15"
    assert ranges(range(2, 5)) == "parts 2 to 4"


def units(text: str) -> bytes:
    """The UTF-16 code units of ``text`` (what JavaScript sees): a character outside the Basic Multilingual Plane is two."""
    return text.encode("utf-16-le", "surrogatepass")


def parts_of(text: str, size: int) -> list[bytes]:
    """The text cut every ``size`` UTF-16 code units, as a script cuts it (so a cut may fall inside a pair), as stored parts."""
    data = units(text)
    return [file_parts.to_bytes(data[i:i + 2 * size].decode("utf-16-le", "surrogatepass")) for i in range(0, len(data), 2 * size)]


def test_parts_are_put_together_after_the_line_count_and_the_checksum_are_checked():
    text = "\ufeff" + "Folder,Card\r\n" + "my cards,Jötun Grunt \U0001F600\r\n" * 40
    chunks = parts_of(text, 37)  # a cut falls inside the pair: half at the end of one part, half at the start of the next
    assert any(c.endswith(b"\xed\xa0\xbd") for c in chunks), "the vector must split a pair"
    assert assemble(chunks, line_count(text), checksum(text)) == text.encode("utf-8")
    with pytest.raises(Mismatch) as lines:
        assemble(chunks, line_count(text) + 1, checksum(text))
    assert lines.value.reason == "lines"
    with pytest.raises(Mismatch) as truncated:
        assemble(chunks[:-1], line_count(text), checksum(text))  # a read cut short: fewer lines than the file has
    assert truncated.value.reason == "lines"
    with pytest.raises(Mismatch) as wrong_sum:
        assemble(chunks, line_count(text), checksum(text) ^ 1)
    assert wrong_sum.value.reason == "checksum"
    altered = list(chunks)
    altered[3] = altered[3].replace(b"J", b"K", 1)
    with pytest.raises(Mismatch) as swapped:
        assemble(altered, line_count(text), checksum(text))  # the same length and lines, one letter different
    assert swapped.value.reason == "checksum"


def test_half_a_pair_that_is_never_completed_is_not_valid_text():
    text = "a\nb \ud83d\n"
    with pytest.raises(Mismatch) as half:
        assemble([file_parts.to_bytes(text)], line_count(text), checksum(text))
    assert half.value.reason == "text"


def test_the_set_of_a_file_is_the_persons_and_the_files_own_identity():
    a = file_parts.set_key(1, 99, 4, 10, None)
    assert a == file_parts.set_key(1, 99, 4, 10, None) and a.startswith(file_parts.MARKER) and len(a) == 63
    others = {file_parts.set_key(2, 99, 4, 10, None), file_parts.set_key(1, 98, 4, 10, None), file_parts.set_key(1, 99, 5, 10, None),
              file_parts.set_key(1, 99, 4, 11, None), file_parts.set_key(1, 99, 4, 10, 7)}
    assert a not in others and len(others) == 5


# ---- the script the assistant runs ------------------------------------------------------------------------------------------------

HARNESS = textwrap.dedent(r"""
    const fs = require('fs'), cp = require('child_process');
    const [, , recipePath, filePath, shell, cap] = process.argv;
    const BS = String.fromCharCode(92);
    const code = fs.readFileSync(recipePath, 'utf8').replace('<PATH>', filePath.split('/').join(BS));
    const calls = [], printed = []; let shellCalls = 0;
    const fileText = () => { const t = fs.readFileSync(filePath).toString('utf8'); return t.charCodeAt(0) === 0xFEFF ? t.slice(1) : t; };
    const escape = s => JSON.stringify(s).replace(/[^ -~]/g, c => BS + 'u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
    function standIn(cmd) {  // what the two PowerShell commands print, computed here
      const t = fileText();
      const plan = /\$i=(\d+);/.exec(cmd);
      if (plan) {
        const N = Number(plan[1]), c = [0]; let i = N;
        while (i < t.length) { if (t.charCodeAt(i) >= 0xDC00 && t.charCodeAt(i) <= 0xDFFF) i--; c.push(i); i += N; }
        c.push(t.length);
        return (t.split('\n').length - 1) + ',' + c.join(',');
      }
      const read = /Substring\((\d+),(\d+)\)/.exec(cmd);
      return escape(t.substr(Number(read[1]), Number(read[2])));
    }
    function real(cmd) {
      const r = cp.spawnSync(shell, ['-NoProfile', '-NonInteractive', '-Command', cmd], {encoding: 'utf8'});
      return r.stdout + r.stderr;
    }
    const tools = {
      exec_command: async ({cmd, login}) => {
        shellCalls++;
        if (login !== false) throw new Error('login must be false: the profile prints noise before the output');
        let out = shell === 'standin' ? standIn(cmd) : real(cmd);
        let tokens = Math.ceil(out.length / 2.5);
        if (cap && out.length > Number(cap)) { out = out.slice(0, Number(cap)); tokens = 20000; }  // a truncated read
        return {output: out, original_token_count: tokens, exit_code: 0, wall_time_seconds: 0.2};
      },
      mcp__the_vault__import_collection_file: async (a) => {
        calls.push(a);
        const same = calls.filter(c => c.parts === a.parts);
        const done = new Set(same.map(c => c.part)).size === a.parts;
        return {structuredContent: {status: done ? 'uploaded' : 'part_received'}};
      },
    };
    const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
    new AsyncFunction('tools', 'text', code)(tools, v => printed.push(v)).then(() => {
      console.log(JSON.stringify({calls, printed, shellCalls}));
    });
""")


def run_recipe(tmp_path, text: str, shell: str = "standin", cap: int | None = None, name: str = "my cards.csv") -> dict:
    """Run the recipe in Node over a file made of ``text`` (written as UTF-8, with a BOM when ``text`` begins with one)."""
    file = tmp_path / name
    file.write_bytes(text.encode("utf-8"))
    (tmp_path / "recipe.js").write_text(file_parts.recipe(), encoding="utf-8")
    (tmp_path / "harness.js").write_text(HARNESS, encoding="utf-8")
    args = [NODE, str(tmp_path / "harness.js"), str(tmp_path / "recipe.js"), str(file).replace("\\", "/"), shell]
    done = subprocess.run(args + ([str(cap)] if cap else []), capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def sample_text(rows: int = 400) -> str:
    head = "\ufeffFolder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,Condition,Printing,Language\r\n"
    return head + "".join(f'my cards,1,0,"Jötun Grunt {i} \U0001F600 \'x\'",XYZ,Invented Set,{i},Mint,Normal,English\r\n' for i in range(rows))


needs_node = pytest.mark.skipif(NODE is None, reason="Node is not installed")


@needs_node
def test_the_script_sends_every_part_with_the_files_own_line_count_and_the_checksum_of_the_whole(tmp_path):
    text = sample_text()
    ran = run_recipe(tmp_path, text)
    calls = sorted(ran["calls"], key=lambda c: c["part"])
    n = len(calls)
    assert n > 1 and [c["part"] for c in calls] == list(range(1, n + 1)) and {c["parts"] for c in calls} == {n}
    sent = "".join(c["text"] for c in calls)
    assert sent == text[1:]  # the shell's ReadAllText drops the byte-order mark; everything else is as in the file, CRLF included
    assert {c["lines"] for c in calls} == {line_count(text)}
    assert {c["sum"] for c in calls} == {checksum(sent)}  # the JavaScript checksum is the server's
    assert json.loads(ran["printed"][0])["status"] == "uploaded"
    assert all(len(c["text"]) <= file_parts.PART_CHARS for c in calls)


@needs_node
def test_a_cut_between_the_two_halves_of_a_character_is_moved_so_no_part_holds_half_of_one(tmp_path):
    cut = file_parts.PART_CHARS
    text = "\ufeffa,b\r\n" + "x" * (cut - 6) + "\U0001F600" + "y,z\r\n" * 30
    assert units(text[1:])[2 * (cut - 1):2 * cut] == units("\ud83d")  # the first cut falls between the halves
    ran = run_recipe(tmp_path, text)
    calls = sorted(ran["calls"], key=lambda c: c["part"])
    for c in calls:
        assert not 0xD800 <= ord(c["text"][-1]) <= 0xDBFF and not 0xDC00 <= ord(c["text"][0]) <= 0xDFFF, "a part was cut inside a pair"
    assert units("".join(c["text"] for c in calls)) == units(text[1:])


@needs_node
def test_a_read_that_comes_back_truncated_is_read_again_in_halves_and_nothing_truncated_is_sent(tmp_path):
    text = sample_text(600)
    ran = run_recipe(tmp_path, text, cap=6000)  # the shell's output budget is smaller than a first-size part
    calls = ran["calls"]
    assert max(len(c["text"]) for c in calls) <= 4096 and len({c["parts"] for c in calls}) == 1  # one consistent set, smaller parts
    assert "".join(c["text"] for c in sorted(calls, key=lambda c: c["part"])) == text[1:]
    assert json.loads(ran["printed"][0])["status"] == "uploaded"


@needs_node
def test_a_file_whose_reads_are_always_truncated_sends_nothing(tmp_path):
    ran = run_recipe(tmp_path, sample_text(300), cap=200)
    assert ran["calls"] == [] and json.loads(ran["printed"][0]) == {"status": "not_read"}


@needs_node
def test_the_javascript_checksum_is_the_server_checksum_for_every_vector(tmp_path):
    recipe = file_parts.recipe()
    js = tmp_path / "crc.js"
    js.write_text(recipe[recipe.index("const T=[]"):recipe.index("const last=")]
                  + "\nconst out=[];for(const s of JSON.parse(require('fs').readFileSync(process.argv[2],'utf8')))out.push(crc([s]));"
                    "console.log(JSON.stringify(out));", encoding="utf-8")
    texts = [t for t, _ in VECTORS] + [sample_text(5)]
    (tmp_path / "in.json").write_text(json.dumps(texts), encoding="utf-8")  # a lone surrogate is written as an escape
    got = json.loads(subprocess.run([NODE, str(js), str(tmp_path / "in.json")], capture_output=True, text=True, check=True).stdout)
    assert got == [checksum(t) for t in texts]


@pytest.mark.skipif(NODE is None or POWERSHELL is None, reason="needs Node and PowerShell (the owner's PC; a Linux lane has neither)")
def test_the_script_works_with_the_real_powershell_for_a_path_with_spaces_and_an_apostrophe(tmp_path):
    text = sample_text(500)
    ran = run_recipe(tmp_path, text, shell=POWERSHELL, name="my cards, the owner's.csv")
    calls = sorted(ran["calls"], key=lambda c: c["part"])
    assert "".join(c["text"] for c in calls) == text[1:] and {c["sum"] for c in calls} == {checksum(text[1:])}
    assert {c["lines"] for c in calls} == {line_count(text)}


def test_the_steps_for_the_model_say_what_to_do_and_tell_the_person_nothing_to_do():
    steps = file_parts.steps()
    assert "<PATH>" in steps and "exec_command" in steps and "confirm_staged_upload" in steps and "Never send the person to a link" in steps
    assert "login:false" in steps and "original_token_count" in steps and str(file_parts.PART_CHARS) in steps
    assert "@PART@" not in steps and "@@N" not in steps and "@PARALLEL@" not in steps and "@MIN@" not in steps

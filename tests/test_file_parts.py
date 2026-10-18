"""A collection file sent as text in ordered parts (vault-0n9.3): the pure parts, without a database.

The checksum is CRC-32 over the UTF-16 code units of the whole text (two bytes each, low byte first). The vectors below were computed
with an independent bitwise implementation (not ``zlib``) and checked with Node's ``zlib.crc32``, and the script a coding assistant runs
(``file_parts.recipe()``) is executed with Node, against a stand-in for the assistant's shell, and against the real PowerShell where
there is one (the owner's PC).
"""

from __future__ import annotations

import json
import os
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


def chars_of(text: str) -> int:
    return len(units(text)) // 2


def test_parts_are_put_together_after_the_line_count_the_length_and_the_checksum_are_checked():
    text = "\ufeff" + "Folder,Card\r\n" + "my cards,Jötun Grunt \U0001F600\r\n" * 40
    chunks = parts_of(text, 37)  # a cut falls inside the pair: half at the end of one part, half at the start of the next
    assert any(c.endswith(b"\xed\xa0\xbd") for c in chunks), "the vector must split a pair"
    n, c, s = line_count(text), chars_of(text), checksum(text)
    assert assemble(chunks, n, c, s) == text.encode("utf-8")
    with pytest.raises(Mismatch) as lines:
        assemble(chunks, n + 1, c, s)
    assert lines.value.reason == "lines"
    with pytest.raises(Mismatch) as truncated:
        assemble(chunks[:-1], n, c, s)  # a read cut short: fewer lines than the file has
    assert truncated.value.reason == "lines"
    with pytest.raises(Mismatch) as wrong_length:
        assemble(chunks, n, c + 1, s)
    assert wrong_length.value.reason == "length"
    with pytest.raises(Mismatch) as wrong_sum:
        assemble(chunks, n, c, s ^ 1)
    assert wrong_sum.value.reason == "checksum"
    altered = list(chunks)
    altered[3] = altered[3].replace(b"J", b"K", 1)
    with pytest.raises(Mismatch) as swapped:
        assemble(altered, n, c, s)  # the same length and lines, one letter different
    assert swapped.value.reason == "checksum"


def test_a_self_consistent_truncated_text_is_refused_because_the_length_comes_from_a_separate_read():
    """Review of PR 483 (M4): lines and sum computed from what was read agree with a cut text; the length of the whole file, read
    on its own, does not."""
    whole = "Folder,Card\r\n" + "my cards,Sol Ring\r\n" * 40
    cut = whole[:-9]  # the last row has no line feed and is cut short
    chunks = parts_of(cut, 100)
    assert assemble(chunks, line_count(cut), chars_of(cut), checksum(cut)) == cut.encode()  # what a cut text claims about itself
    with pytest.raises(Mismatch) as caught:
        assemble(chunks, line_count(cut), chars_of(whole), checksum(cut))
    assert caught.value.reason == "length"


def test_half_a_pair_that_is_never_completed_is_not_valid_text():
    text = "a\nb \ud83d\n"
    with pytest.raises(Mismatch) as half:
        assemble([file_parts.to_bytes(text)], line_count(text), chars_of(text), checksum(text))
    assert half.value.reason == "text"


def test_the_set_of_a_file_is_the_persons_and_the_files_own_identity():
    a = file_parts.set_key(1, 99, 4, 10, 500, None)
    assert a == file_parts.set_key(1, 99, 4, 10, 500, None) and a.startswith(file_parts.MARKER) and len(a) == 63
    others = {file_parts.set_key(2, 99, 4, 10, 500, None), file_parts.set_key(1, 98, 4, 10, 500, None), file_parts.set_key(1, 99, 5, 10, 500, None),
              file_parts.set_key(1, 99, 4, 11, 500, None), file_parts.set_key(1, 99, 4, 10, 501, None), file_parts.set_key(1, 99, 4, 10, 500, 7)}
    assert a not in others and len(others) == 6


# ---- the script the assistant runs ------------------------------------------------------------------------------------------------

HARNESS = textwrap.dedent(r"""
    const fs = require('fs'), cp = require('child_process');
    const [, , recipePath, filePath, shell] = process.argv;
    const opts = JSON.parse(process.env.HARNESS_OPTS || '{}');
    const BS = String.fromCharCode(92);
    const winPath = process.platform === 'win32' ? filePath.split('/').join(BS) : filePath;
    const code = fs.readFileSync(recipePath, 'utf8').replace('<PATH>', () => JSON.stringify(winPath).slice(1, -1));
    const calls = [], printed = [], cmds = []; let shellCalls = 0, sends = 0;
    const strict = new TextDecoder('utf-8', {fatal: true});
    const escape = s => JSON.stringify(s).replace(/[^ -~]/g, c => BS + 'u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
    function standIn(cmd) {  // what the PowerShell commands print, computed here
      if (cmd.startsWith('Start-Sleep')) return '';
      const hex = /^\$h='([0-9a-f]+)';/.exec(cmd);
      if (!hex) throw new Error('a command that does not begin by decoding the path from hex digits');
      let named = ''; for (let i = 0; i < hex[1].length; i += 4) named += String.fromCharCode(parseInt(hex[1].slice(i, i + 4), 16));
      if (named !== winPath) throw new Error('the shell was given another path: ' + named);
      if (cmd.includes('FileInfo')) return String(opts.fake_size || fs.statSync(filePath).size);  // the length, without reading the file
      const bytes = fs.readFileSync(filePath);
      if (cmd.includes('ReadAllBytes') && opts.fake_size) return '1000,10,' + opts.fake_size + ',0';  // a file of any size, without making one
      if (cmd.includes('ReadAllBytes')) {
        const o = bytes.length >= 3 && bytes[0] === 239 && bytes[1] === 187 && bytes[2] === 191 ? 3 : 0;
        let t; try { t = strict.decode(bytes.subarray(o)); } catch (e) { return 'Exception calling GetString: invalid UTF-8'; }
        return t.length + ',' + (t.split('\n').length - 1) + ',' + (bytes.length - o) + ',' + o;
      }
      const geo = /Min\(\$f\.Length,(\d+)\+\$k\*(\d+)\)/.exec(cmd), ks = /\$s=\(b (\d+)\);\$e=\(b (\d+)\)/.exec(cmd);
      const O = Number(geo[1]), N = Number(geo[2]);
      const b = k => {
        if (k <= 0) return O;
        let q = Math.min(bytes.length, O + k * N);
        if (q >= bytes.length) return bytes.length;
        for (let i = 0; i < 3; i++) { const c = bytes[q]; if (c === undefined || c < 128 || c > 191) break; q++; }
        return q;
      };
      let part = strict.decode(bytes.subarray(b(Number(ks[1])), b(Number(ks[2]))));
      if (opts.drop_last_row && Number(ks[2]) * N + O >= bytes.length) part = part.slice(0, -9);  // a silent short read
      return escape(part);
    }
    const real = cmd => new Promise(res =>  // asynchronous, as the app runs them: eight at a time really are eight processes
      cp.execFile(shell, ['-NoProfile', '-NonInteractive', '-Command', cmd], {encoding: 'utf8', maxBuffer: 1 << 26}, (e, so, se) => res(so + se)));
    const tools = {
      exec_command: async ({cmd, login}) => {
        shellCalls++; cmds.push(cmd);
        if (login !== false) throw new Error('login must be false: the profile prints noise before the output');
        let out = shell === 'standin' ? standIn(cmd) : await real(cmd);
        let tokens = Math.ceil(out.length / 2.5);
        if (opts.cap && out.length > opts.cap) { out = out.slice(0, opts.cap); tokens = 20000; }  // a truncated read
        return {output: out, original_token_count: tokens, exit_code: 0, wall_time_seconds: 0.2};
      },
      mcp__the_vault__import_collection_file: async (a) => {
        sends++;
        if (opts.throttle_part === a.part && (opts.throttled = (opts.throttled || 0) + 1) <= opts.throttle_times) {
          return {isError: true, content: [{type: 'text', text: 'Too many file intake part requests'}], structuredContent: {status: 429, retry_after_seconds: 1}};
        }
        if (opts.fail_part === a.part) return {isError: true, content: [{type: 'text', text: 'boom'}]};
        if (opts.answer_part === a.part) return {structuredContent: {status: opts.answer_status, changed: false, say: 'The Vault says: ' + opts.answer_status}};
        calls.push(a);
        const same = calls.filter(c => c.parts === a.parts);
        const done = new Set(same.map(c => c.part)).size === a.parts;
        return {structuredContent: {status: done ? 'uploaded' : 'part_received'}};
      },
    };
    const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
    new AsyncFunction('tools', 'text', code)(tools, v => printed.push(v)).then(() => {
      console.log(JSON.stringify({calls, printed, shellCalls, cmds, sends}));
    });
""")


def run_recipe(tmp_path, text: str, shell: str = "standin", name: str = "my cards.csv", bucket_id: int | None = None, raw: bytes | None = None,
               **opts) -> dict:
    """Run the recipe in Node over a file made of ``text`` (UTF-8, with a byte-order mark when ``text`` begins with one) or of ``raw``."""
    file = tmp_path / name
    file.write_bytes(text.encode("utf-8") if raw is None else raw)
    (tmp_path / "recipe.js").write_text(file_parts.recipe(bucket_id), encoding="utf-8")
    (tmp_path / "harness.js").write_text(HARNESS, encoding="utf-8")
    args = [NODE, str(tmp_path / "harness.js"), str(tmp_path / "recipe.js"), str(file).replace("\\", "/"), shell]
    done = subprocess.run(args, capture_output=True, text=True, timeout=300, cwd=tmp_path, env={**os.environ, "HARNESS_OPTS": json.dumps(opts)})
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def sample_text(rows: int = 400) -> str:
    head = "\ufeffFolder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,Condition,Printing,Language\r\n"
    return head + "".join(f'my cards,1,0,"Jötun Grunt {i} \U0001F600 \'x\'",XYZ,Invented Set,{i},Mint,Normal,English\r\n' for i in range(rows))


needs_node = pytest.mark.skipif(NODE is None, reason="Node is not installed")


def sent_text(ran: dict) -> str:
    return "".join(c["text"] for c in sorted(ran["calls"], key=lambda c: c["part"]))


@needs_node
def test_the_script_sends_every_part_with_the_files_own_length_line_count_and_the_checksum_of_the_whole(tmp_path):
    text = sample_text()
    ran = run_recipe(tmp_path, text)
    calls = sorted(ran["calls"], key=lambda c: c["part"])
    n = len(calls)
    assert n > 1 and [c["part"] for c in calls] == list(range(1, n + 1)) and {c["parts"] for c in calls} == {n}
    sent = sent_text(ran)
    assert sent == text[1:]  # the byte-order mark is dropped; everything else is as in the file, CRLF included
    assert {c["lines"] for c in calls} == {line_count(text)} and {c["chars"] for c in calls} == {chars_of(text[1:])}
    assert {c["sum"] for c in calls} == {checksum(sent)}  # the JavaScript checksum is the server's
    assert json.loads(ran["printed"][0])["status"] == "uploaded"
    assert all(len(c["text"].encode("utf-8")) <= file_parts.PART_BYTES for c in calls)


@needs_node
def test_a_part_is_cut_at_a_character_boundary_so_no_part_holds_half_of_a_character(tmp_path):
    cut = file_parts.PART_BYTES
    text = "\ufeffa,b\r\n" + "x" * (cut - 6) + "\U0001F600é" + "y,z\r\n" * 30  # a four-byte character across the first cut
    assert 0x80 <= text[1:].encode("utf-8")[cut] <= 0xBF  # the first cut falls inside a character
    ran = run_recipe(tmp_path, text)
    assert sent_text(ran) == text[1:] and len(ran["calls"]) >= 2


@needs_node
def test_a_path_never_reaches_a_command_as_text_whatever_the_name_holds(tmp_path):
    """Review of PR 483 (H1): PowerShell reads U+2018, U+2019, U+201A and U+201B as quotes, so doubling the ASCII quote let a file name end
    the string and run commands. The path is now hex digits in every command. The stand-in fails the run if a command carries any other
    path; the commands are searched for the name too."""
    names = ["a’);Set-Content inj.txt INJECTED;(’b.csv", "q‘‚‛'.csv", "cards${1+1}.csv", "my `cards` $HOME $(whoami).csv",
             "owner's cards (2026); ls.csv", "nö \U0001F600 café.csv"]
    if os.name != "nt":
        names.append("line\nbreak\"quote.csv")
    for name in names:
        ran = run_recipe(tmp_path, sample_text(20), name=name)
        assert json.loads(ran["printed"][0])["status"] == "uploaded", name
        assert not any(name in c or name.replace("'", "''") in c for c in ran["cmds"]), name
        assert not (tmp_path / "inj.txt").exists()
    recipe = file_parts.recipe()
    assert "String.raw" not in recipe and "replace(/'/g" not in recipe and "charCodeAt(i).toString(16)" in recipe
    assert "JavaScript string in double quotes" in file_parts.steps()


@needs_node
def test_every_part_goes_into_the_bucket_the_call_was_made_for(tmp_path):
    """Review of PR 483: the script never sent bucket_id, so a call that named a bucket staged a whole-collection preview."""
    ran = run_recipe(tmp_path, sample_text(400), bucket_id=7)
    assert len(ran["calls"]) > 1 and {c["bucket_id"] for c in ran["calls"]} == {7}
    assert all("bucket_id" not in c for c in run_recipe(tmp_path, sample_text(400))["calls"])
    assert ",bucket_id:7" in file_parts.steps(7) and "bucket_id" not in file_parts.steps()


@needs_node
def test_a_read_that_comes_back_truncated_is_read_again_in_halves_and_nothing_truncated_is_sent(tmp_path):
    text = sample_text(600)
    ran = run_recipe(tmp_path, text, cap=12000)  # the shell's output budget is smaller than a first-size part
    calls = ran["calls"]
    assert max(len(c["text"].encode("utf-8")) for c in calls) <= file_parts.MIN_PART_BYTES and len({c["parts"] for c in calls}) == 1
    assert sent_text(ran) == text[1:] and json.loads(ran["printed"][0])["status"] == "uploaded"


@needs_node
def test_a_file_whose_reads_are_always_truncated_sends_nothing(tmp_path):
    ran = run_recipe(tmp_path, sample_text(300), cap=200)
    assert ran["calls"] == [] and json.loads(ran["printed"][0]) == {"status": "not_read"}


@needs_node
def test_a_truncated_first_read_sends_nothing_because_the_length_and_line_count_cannot_be_trusted(tmp_path):
    """Review of PR 483 (thread 1): the offsets list was read in one capped output; a truncated list could parse and make its last offset
    the file's length. Now the length and line count come from a four-number read of their own, and a cut one is not used."""
    ran = run_recipe(tmp_path, sample_text(300), cap=6)  # the four numbers are longer than 6 characters
    assert ran["calls"] == [] and json.loads(ran["printed"][0]) == {"status": "not_read"} and ran["shellCalls"] == 2  # the length, then the cut read


@needs_node
def test_a_short_read_that_nothing_flags_is_caught_by_the_length_and_line_count_of_the_first_read(tmp_path):
    ran = run_recipe(tmp_path, sample_text(300), drop_last_row=True)
    assert ran["calls"] == [] and json.loads(ran["printed"][0]) == {"status": "not_read"}


@needs_node
def test_a_file_that_is_not_utf8_is_not_read_and_the_names_of_other_encodings_are_not_guessed(tmp_path):
    ran = run_recipe(tmp_path, "", raw=b"Folder Name,Card Name\r\nmy cards,Caf\xe9\r\n")
    assert ran["calls"] == [] and json.loads(ran["printed"][0]) == {"status": "not_read"}


@needs_node
def test_a_throttled_part_is_sent_again_after_the_wait_and_a_failed_one_stops_the_script_naming_it(tmp_path):
    text = sample_text(200)
    throttled = run_recipe(tmp_path, text, throttle_part=2, throttle_times=2)
    assert sent_text(throttled) == text[1:] and json.loads(throttled["printed"][0])["status"] == "uploaded"
    assert any(c.startswith("Start-Sleep -Seconds 1") for c in throttled["cmds"]) and throttled["sends"] == len(throttled["calls"]) + 2
    failed = run_recipe(tmp_path, sample_text(2000), fail_part=3)
    stopped = json.loads(failed["printed"][0])
    assert stopped == {"status": "stopped", "failed_parts": [3]}
    assert sorted(c["part"] for c in failed["calls"]) == [1, 2, 4, 5, 6, 7, 8]  # the group went through; no group after the failing one


@needs_node
def test_the_smallest_part_size_keeps_a_pass_inside_the_default_part_allowance():
    from vault import config
    passes = config.DEFAULT_IMPORT_MAX_BYTES // file_parts.MIN_PART_BYTES
    assert passes == 1024 and passes <= config.DEFAULT_IMPORT_PART_LIMIT and file_parts.max_parts() >= passes


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


@pytest.mark.skipif(NODE is None or POWERSHELL is None, reason="needs Node and PowerShell (the owner's PC; the CI runner has pwsh 7)")
@pytest.mark.parametrize("name", ["my cards, the owner's.csv", "a’);Set-Content inj.txt INJECTED;(’b.csv", "dán‘s `cards` $(whoami) ; x.csv"])
def test_the_script_works_with_the_real_powershell_and_a_name_cannot_run_anything(tmp_path, name):
    """The recipe runs under the real shell: parts equal the file, the length and checksum are the server's, and the name that ran an
    injected command in the review (curly quotes ending the string) creates no file."""
    text = sample_text(500)
    ran = run_recipe(tmp_path, text, shell=POWERSHELL, name=name)
    calls = sorted(ran["calls"], key=lambda c: c["part"])
    assert sent_text(ran) == text[1:] and {c["sum"] for c in calls} == {checksum(text[1:])}
    assert {c["lines"] for c in calls} == {line_count(text)} and {c["chars"] for c in calls} == {chars_of(text[1:])}
    assert not (tmp_path / "inj.txt").exists()


def test_the_steps_for_the_model_say_what_to_do_and_tell_the_person_nothing_to_do():
    steps = file_parts.steps()
    assert "<PATH>" in steps and "exec_command" in steps and "confirm_staged_upload" in steps and "Never send the person to a link" in steps
    assert "login:false" in file_parts.recipe() and "original_token_count" in file_parts.recipe() and str(file_parts.PART_BYTES) in steps
    assert "PowerShell" in steps and "macOS or Linux" in steps and "give the person `say`" in steps  # the limit is stated, not hidden
    assert "already_staged" in steps and "get_staged_upload" in steps
    assert "@PART@" not in steps and "@PARALLEL@" not in steps and "@MIN@" not in steps and "@BUCKET@" not in steps


@needs_node
@pytest.mark.parametrize("status", ["bad_part", "too_large", "unreadable", "mismatch", "cannot_receive"])
def test_the_script_stops_at_the_first_answer_that_is_not_progress_and_prints_the_vaults_sentence(tmp_path, status):
    """Review of the delta of PR 483: the script did not stop on bad_part, too_large or unreadable. It sends no group after the one in
    which such an answer came, and prints the answer, whose `say` is what the person is told."""
    ran = run_recipe(tmp_path, sample_text(3000), answer_part=2, answer_status=status)
    printed = json.loads(ran["printed"][0])
    assert printed["status"] == status and printed["say"] == "The Vault says: " + status
    assert ran["sends"] <= file_parts.PARALLEL < len(sample_text(3000).encode()) // file_parts.PART_BYTES  # the group it was in, no more


@needs_node
def test_a_file_over_the_size_limit_or_in_too_many_parts_is_not_read_at_all(tmp_path, monkeypatch):
    """Review of the delta of PR 483: a mocked 200 MB file produced 12,208 reads and sends. The size comes from the first small read."""
    ran = run_recipe(tmp_path, sample_text(10), fake_size=200_000_000)
    printed = json.loads(ran["printed"][0])
    assert printed["status"] == "too_large" and "8 MB" in printed["say"] and printed["say"].count(".") == 1
    assert ran["shellCalls"] == 1 and ran["sends"] == 0  # the one small read, nothing else
    monkeypatch.setenv("IMPORT_MAX_BYTES", str(64 * 1024 * 1024))  # a raised limit: 32 MiB is inside it but is 2,048 parts of 16 KiB
    ran = run_recipe(tmp_path, sample_text(10), fake_size=32 * 1024 * 1024)
    assert json.loads(ran["printed"][0])["status"] == "too_large" and ran["shellCalls"] == 2 and ran["sends"] == 0  # length, then the small read
    assert 8 * 1024 * 1024 // file_parts.MIN_PART_BYTES == file_parts.MAX_PASS_PARTS


def test_the_steps_say_what_to_do_with_each_result_the_script_can_print():
    steps = file_parts.steps()
    assert "not_read" in steps and "give the person the sentence in the `say` you started from, and stop, with no link" in steps
    assert "stops at the first answer that is not progress" in steps and "one sentence in its `say`" in steps


def test_where_ci_runs_the_tests_it_has_node_and_powershell_so_the_real_shell_tests_run_and_are_not_skipped():
    """The tests above that need a real PowerShell are skipped where there is none. On the GitHub runner they must run."""
    if os.environ.get("GITHUB_ACTIONS"):
        assert NODE and POWERSHELL, "the runner has no Node or no PowerShell: the real-shell tests would be skipped"


@needs_node
def test_a_file_far_over_the_limit_is_refused_from_its_length_alone_without_being_read(tmp_path):
    """Review of PR 483 (thread, L3/L4): the first command read the whole file, decoded and split it before JavaScript looked at a size.
    The first command now asks only for the file's length (FileInfo), and nothing else runs when it is over the limit: a 12 MiB file."""
    ran = run_recipe(tmp_path, "", raw=b"x" * (12 * 1024 * 1024))
    printed = json.loads(ran["printed"][0])
    assert printed["status"] == "too_large" and printed["say"].count(".") == 1
    assert ran["shellCalls"] == 1 and "FileInfo" in ran["cmds"][0] and "ReadAllBytes" not in ran["cmds"][0] and ran["sends"] == 0
    # over the limit even after allowing for a byte-order mark (three bytes that are not counted)
    limit = 8 * 1024 * 1024
    assert json.loads(run_recipe(tmp_path, "", raw=b"y" * (limit + 4))["printed"][0])["status"] == "too_large"

"""M1 관측기 — PostToolUse hook 진입점 (C2 술어 엔진 + 얇은 C1 캐시, design.md §5/§8).

그림자 모드다: 술어를 돌리되 **아무것도 차단하지 않는다.** 판정은
`<repo>/.claude/state/audit/log.jsonl` 에 한 줄씩 append 될 뿐이다.

이 스크립트는 사용자 레벨 hook 으로 절대경로 실행된다
(`python3 <repo>/src/drift_detect/observer.py`, docs/decisions/0002). 직접
실행되면 파이썬이 이 파일이 있는 디렉터리만 sys.path 에 넣으므로
`drift_detect` 패키지(부모 디렉터리) 자체는 아직 import 할 수 없다 — 그 간극은
loader 모듈이 메운다. 패키지로 정상 import 된 경우(테스트 등)에는
`drift_detect.loader` 가 이미 보이므로 그 경로를 그대로 쓴다.

**아무것도 차단하지 않는다.** 최상위를 BaseException 까지 잡고 항상 exit 0
이며, stdout 에는 아무것도 쓰지 않는다 — hook 출력은 모델 컨텍스트로 들어가고,
이 hook 은 관측 전용이라 거기 낄 자리가 없다.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    from drift_detect.loader import ensure_on_path
except ImportError:
    from loader import ensure_on_path  # 스크립트 직접 실행 시 (같은 디렉터리, bare import)

_SRC_AVAILABLE = ensure_on_path()

try:
    from drift_detect.compiler import (
        compile_contract,
        detect_branch,
        resolve_policy_path,
    )
    from drift_detect.predicates import evaluate
except ImportError:
    compile_contract = None
    detect_branch = None
    evaluate = None
    resolve_policy_path = None

_RELEVANT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit", "Bash"})
_COMMAND_SUMMARY_MAX = 160
_GIT_TIMEOUT_SEC = 5


def _first_str(d, keys):
    for key in keys:
        value = d.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _summarize_command(command):
    """명령 "요약" — 전문을 로그에 넣지 않는다(민감정보·크기 양쪽 이유)."""
    if not command:
        return None
    command = command.strip()
    if len(command) <= _COMMAND_SUMMARY_MAX:
        return command
    return command[:_COMMAND_SUMMARY_MAX] + "…"


def _target_path(tool_input, repo_root):
    """Edit/Write/MultiEdit/NotebookEdit 대상 경로를 레포 루트 상대 POSIX 로."""
    raw = _first_str(tool_input, ("file_path", "notebook_path", "path"))
    if raw is None:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = repo_root / p
    try:
        return p.relative_to(repo_root).as_posix()
    except ValueError:
        pass
    try:
        return p.resolve().relative_to(repo_root.resolve()).as_posix()
    except (ValueError, OSError):
        return None  # 레포 밖 — 이 계약의 판정 대상이 아니다


def _git_rev_parse(cwd, *args):
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SEC,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    return out or None


def _repo_root(cwd):
    """cwd 에서 레포 루트를 얻는다.

    `.git` 이 파일인 대상(submodule)에서 디렉터리로 가정하고 경로를 수작업
    조립하면 조용히 틀린 루트를 만든다(design.md §9 리스크: "레포 구조 가정").
    git 자신에게 묻는다 — `--git-common-dir` 로 유효한 git 컨텍스트인지 먼저
    확인하고(파일/디렉터리 형태를 가리지 않는다), 실제 작업 트리 루트는
    `--show-toplevel` 로 얻는다. 이 값 역시 submodule·worktree 양쪽에서 정확한
    루트를 반환한다(git 이 내부적으로 처리) — 우리가 직접 `.git` 경로를 파싱할
    필요가 없다.
    """
    if _git_rev_parse(cwd, "--git-common-dir") is None:
        return None
    toplevel = _git_rev_parse(cwd, "--show-toplevel")
    if toplevel is None:
        return None
    return Path(toplevel)


def _read_json(path):
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _sha256_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cache_is_fresh(meta, repo_root):
    """meta(contract.meta.json) 가 지금 레포 상태와 여전히 일치하는가."""
    if not isinstance(meta, dict):
        return False
    try:
        source_path = meta.get("source_path")
        if not source_path:
            return False
        plan_path = repo_root / source_path
        if not plan_path.is_file():
            return False
        if plan_path.stat().st_mtime != meta.get("mtime"):
            return False
        if _sha256_file(plan_path) != meta.get("sha256"):
            return False
    except OSError:
        return False

    meta_policy_path = meta.get("policy_path")
    if meta_policy_path:
        # 0003 개정 이후 policy_path 는 대상 레포 상대경로가 아니라 drift-detect
        # 정책 디렉터리의 절대경로다 (compiler._load_repo_escalation_policy).
        try:
            policy_path = Path(meta_policy_path)
            if not policy_path.is_file():
                return False
            if policy_path.stat().st_mtime != meta.get("policy_mtime"):
                return False
            if _sha256_file(policy_path) != meta.get("policy_sha256"):
                return False
        except OSError:
            return False
    else:
        # meta 에는 정책 파일이 없었는데 지금은 생겼으면 캐시 무효
        # (docs/decisions/0001 필수 조건 4 — 정책 파일 신설도 재컴파일 대상).
        policy_path = resolve_policy_path(repo_root) if resolve_policy_path else None
        if policy_path is not None and policy_path.is_file():
            return False
    return True


def _write_contract_cache(state_dir, contract, meta):
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "contract.json").write_text(
            json.dumps(contract, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (state_dir / "contract.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except OSError:
        pass  # 캐시 쓰기 실패는 치명적이지 않다 — 다음 호출이 다시 컴파일한다


def _load_or_compile_contract(repo_root, branch):
    """contract.meta.json 의 mtime·해시로 캐시 유효성을 판단해 재컴파일 여부를 정한다."""
    state_dir = repo_root / ".claude" / "state"
    cached_contract = _read_json(state_dir / "contract.json")
    cached_meta = _read_json(state_dir / "contract.meta.json")

    if cached_contract is not None and _cache_is_fresh(cached_meta, repo_root):
        return cached_contract

    contract, meta = compile_contract(repo_root, branch=branch)
    _write_contract_cache(state_dir, contract, meta)
    return contract


def _append_log(repo_root, record):
    log_path = repo_root / ".claude" / "state" / "audit" / "log.jsonl"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass  # 로깅 실패가 세션을 막아서는 안 된다


def _contract_source(contract, has_contract):
    if has_contract:
        return "plan"
    if contract and contract.get("policy_deny"):
        return "policy"
    return "none"


def _run(payload):
    if not isinstance(payload, dict):
        return
    if compile_contract is None or detect_branch is None or evaluate is None:
        return  # drift-detect 소스를 못 찾음 — 계약 없음 폴백(0002 필수조건 1)

    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or tool_name not in _RELEVANT_TOOLS:
        return

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}

    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        cwd = os.getcwd()

    repo_root = _repo_root(cwd)
    if repo_root is None:
        return
    branch = detect_branch(repo_root)

    contract = _load_or_compile_contract(repo_root, branch)

    if tool_name == "Bash":
        command = tool_input.get("command")
        command = command if isinstance(command, str) else None
        target = None
    else:
        command = None
        target = _target_path(tool_input, repo_root)

    result = evaluate(target, contract, command=command, cwd=cwd)
    has_contract = bool(contract) and contract.get("status") == "ok"

    record = {
        "ts": time.time(),
        "repo": repo_root.name,
        "branch": branch,
        "tool": tool_name,
        "target": target if target is not None else _summarize_command(command),
        "verdict": result["code"],
        "matched": result["matched"],
        "has_contract": has_contract,
        # 판정 근거: plan=계획 계약, policy=계약 없이 레포 정책 deny 만 적용, none=둘 다 없음.
        # has_contract 의미(계획 계약 존재)는 바꾸지 않는다.
        "contract_source": _contract_source(contract, has_contract),
        "policy_deny": bool(result.get("policy_deny")),
    }
    _append_log(repo_root, record)


def main():
    try:
        try:
            payload = json.load(sys.stdin)
        except Exception:
            payload = None
        _run(payload)
    except BaseException:
        pass  # M1 은 아무것도 차단하지 않는다 — 예외도 예외다
    sys.exit(0)


if __name__ == "__main__":
    main()

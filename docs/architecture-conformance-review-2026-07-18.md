# Architecture and Data Pipeline Conformance Review

## 1. Review Scope

이 리뷰는 `docs/architecture-and-data-pipeline.md`의 설명이 실제 runtime 코드에
반영되어 있는지 확인한다.

- 기준 branch: `codex/verifier-workspace-bundles`
- 기준 main: `origin/main@753d35531b8bf33182abc8bbc6130b124738ae36`
- 추가 검토 범위: 현재 branch의 wire codec 및 workspace bundle 변경
- 검토일: 2026-07-18
- 검토 방법: module import와 composition root 대조, data producer/consumer 추적,
  persistence와 parser 구현 확인, targeted/full regression

Sisyphus MCP의 task index는 이 Harness 작업공간이 아니라 기존 Sisyphus 저장소를
가리켰다. 따라서 이 리뷰에 대응하는 canonical task observation, record,
conformance 상태는 확인하지 못했다. 아래의 `부분 일치` 판정은 Sisyphus lifecycle
색상이 아니라 코드 리뷰 결과다.

## 2. Verdict

**부분 일치**다. agent loop, bounded tools, verifier 실행, queue transaction,
benchmark 공식, GEPA gate, policy HMAC의 핵심 동작은 문서와 일치한다. 그러나
Verifier evidence authority, artifact immutability, adapter 의존 구조, parser
엄격성, container 목표 상태에 관한 일부 문장은 실제 구현보다 강하거나 오래됐다.

| 등급 | 건수 | 의미 |
| --- | ---: | --- |
| P0 | 0 | 즉시 실행을 중단시킬 불일치 없음 |
| P1 | 2 | 신뢰 경계 또는 판정 권위를 잘못 이해하게 하는 불일치 |
| P2 | 4 | 구조·검증 보장을 실제보다 강하게 설명하는 불일치 |
| P3 | 1 | 탐색성과 유지보수성을 떨어뜨리는 drift |

## 3. Findings

### P1-1. Benchmark scoring은 Verifier receipt를 권위 자료로 직접 소비하지 않는다

문서는 `VerificationReceipt`의 소비자에 benchmark를 포함하고, verification
artifact에서 criterion score가 나온다고 설명했다. 실제
`CodingAgentBenchmarkEvaluator`는 `AgentResult.artifact_path`를
`_latest_criterion_pass_rate()`에 전달한다. 이 함수는 agent의 `steps/*.json`에
복제된 `event.criteria`를 파싱하며 Verifier의 `receipt.json`을 읽거나 digest를
검증하지 않는다.

근거:

- `src/sisyphus_harness/benchmarks.py::_latest_criterion_pass_rate`
- `src/sisyphus_harness/agent.py::_verification_event`
- `src/sisyphus_harness/agent.py::LocalCodingAgent._step_payload`
- `docs/adr/0003-verification-authority.md`

영향: agent trace projection이 누락되거나 변조되면 실제 verifier artifact와 다른
score가 계산될 수 있다. Evolve가 Agent와 Verifier 구현에 의존하지 않아야 한다는
목표와 달리, 현재 scoring provenance는 Agent-owned artifact에 종속된다.

조치: 원문을 실제 흐름으로 수정했다. runtime 해결은
`VerificationEvidencePort`와 digest-validated receipt reader를 추가하고,
benchmark/evolve가 해당 port만 사용하게 하는 것이다.

### P1-2. `immutable receipt` 주장은 현재 filesystem 보장보다 강하다

`write_json_atomic()`은 same-directory temporary file, file `fsync`, `os.replace`,
directory `fsync`로 crash-consistent write를 제공한다. `AgentRunStore`와
`BoundedVerifier`도 중복 run directory를 거부한다. 그러나 receipt는 서명되거나
content-addressed storage에 고정되지 않으며 같은 OS 권한의 사용자가 저장 후
수정할 수 있다.

근거:

- `src/sisyphus_harness/receipts.py::write_json_atomic`
- `src/sisyphus_harness/run_store.py::AgentRunStore`
- `src/sisyphus_harness/verifier.py::BoundedVerifier.verify`

영향: 운영자가 `immutable`을 tamper evidence 또는 append-only durability로
해석할 수 있다.

조치: `architecture.md`와 상세 문서의 표현을 `atomically persisted receipt`로
수정했다. service-mode receipt digest와 artifact-store immutability는 ADR 목표로
남아 있다.

### P2-1. Module graph가 adapter/port 분리를 반영하지 않았다

기존 diagram은 CLI, worker, benchmark가 `agent.py`를 직접 의존하고 Agent가
`verifier.py`를 직접 의존한다고 표시했다. 실제 코드는 다음 방향을 사용한다.

```text
CLI / Worker / Benchmark -> InProcessAgentRunFactory
InProcessAgentRunFactory -> LocalCodingAgent + BoundedVerifier adapter
LocalCodingAgent -> VerificationPort
Benchmark -> AgentRunFactoryPort
Verifier -> contracts/verification.py
```

근거:

- `src/sisyphus_harness/adapters/in_process.py`
- `src/sisyphus_harness/ports/agent_run.py`
- `src/sisyphus_harness/ports/verification.py`
- `tests/test_architecture_dependencies.py`

조치: 상세 diagram, composition 설명, change-impact 표를 실제 import와 canonical
contract 위치에 맞게 수정했다.

### P2-2. 모든 외부 parser가 unknown field를 거부한다는 설명은 거짓이다

TOML config, typed coding job, benchmark dataset/case, model decision,
`CandidatePolicy`, `WorkspaceBundleRef`는 bounded schema로 파싱된다. 반면 low-level
queue payload는 임의 object를 허용하고 OpenAI-compatible response envelope는
필요한 field만 읽는다. approval/active policy 최상위 object도 unknown field를
거부하지 않는다.

근거:

- `src/sisyphus_harness/config.py`
- `src/sisyphus_harness/worker.py::CodingJobPayload.from_dict`
- `src/sisyphus_harness/benchmarks.py::load_benchmark_dataset`
- `src/sisyphus_harness/provider.py::OpenAICompatibleProvider.complete`
- `src/sisyphus_harness/policy.py::_read_json_object`

조치: strict 보장을 schema별로 열거하고 예외 경계를 문서에 명시했다.

### P2-3. Policy key와 artifact schema 보장이 과장됐다

새 `authority.key`는 32 byte, mode `0600`으로 생성된다. 기존 key는 bytes를 그대로
읽으며 길이, file type, owner, permission mode를 재검증하지 않는다. approval과
active artifact는 HMAC과 candidate/evolution binding을 검증하지만 top-level
schema version과 unknown field를 strict하게 검증하지 않는다.

근거:

- `src/sisyphus_harness/policy.py::PolicyRegistry._load_or_create_key`
- `src/sisyphus_harness/policy.py::PolicyRegistry.activate`
- `src/sisyphus_harness/policy.py::PolicyRegistry.load_active`

조치: sequence와 한계를 실제 동작에 맞게 수정했다. key metadata 검증과 strict
artifact parser는 후속 control-plane hardening으로 남긴다.

### P2-4. Accepted ADR의 목표 상태와 현재 runtime이 섞여 있었다

ports와 in-process adapters는 구현됐고, 현재 branch에는 안전한 workspace bundle
primitive가 있다. 하지만 runtime request는 여전히 host `Path`를 전달하며 독립
Agent/Verifier/Evolve/Control process, transport adapter,
`VerificationEvidencePort`, sandboxed verifier container는 없다.

근거:

- `src/sisyphus_harness/ports/verification.py`
- `src/sisyphus_harness/ports/agent_run.py`
- `src/sisyphus_harness/infra/workspace_bundle.py`
- `docs/adr/0001-service-boundaries.md`
- `docs/adr/0002-workspace-bundle.md`
- `docs/adr/0003-verification-authority.md`
- `docs/adr/0004-authority-and-artifact-ownership.md`

조치: 상세 문서에 `현재 구현과 ADR 목표 상태` 절을 추가하고 각 미구현 경계를
명시했다.

### P3-1. Commit anchor와 change-impact map이 오래됐다

문서는 runtime 기준을 `47539e0`으로 고정했지만 현재 main은 adapter migration을
포함한 `753d355`다. command contract의 canonical 위치도 `models.py`가 아니라
`contracts/verification.py`다.

조치: 장기 문서에서 commit anchor를 제거하고 이 날짜별 review에 기준 commit을
고정했다. change-impact map은 contracts, ports, adapters, workspace bundle 위치를
반영했다.

## 4. Confirmed Alignment

| 문서 영역 | 판정 | 코드 근거 |
| --- | --- | --- |
| Model/tool 권한 분리 | 일치 | `protocol.py`, `tools.py`, `agent.py` |
| Path containment와 symlink write 차단 | 일치 | `workspace.py`, `tools.py` |
| Criterion-command exact binding | 일치 | `agent.py::_require_criterion_coverage` |
| Deterministic compaction과 stagnation | 일치 | `compaction.py`, `agent.py` |
| Shell 없는 verifier와 mutation detection | 일치 | `verifier.py` |
| SQLite lease와 terminal fencing | 일치 | `database.py`, `queue.py`, `worker.py` |
| Queue side effect at-least-once 한계 | 일치 | lease가 workspace mutation을 취소하지 않음 |
| Benchmark 격리 rollout과 score 공식 | 일치 | `benchmarks.py` |
| Train/holdout 재평가와 acceptance gate | 일치 | `evolution.py` |
| Candidate surface 제한 | 일치 | `contracts/policy.py`, `evolution.py` |
| Local HMAC candidate binding | 일치, 한계 명시 | `policy.py` |
| Git common-dir authority root | 일치 | `authority.py` |
| Workspace bundle 생성과 안전한 추출 | 부분 구현 | `infra/workspace_bundle.py` |

## 5. Remediation Order

| 순서 | 작업 | 상태 |
| ---: | --- | --- |
| 1 | 실제 dependency와 parser/receipt 보장으로 문서 수정 | 이 branch에서 완료 |
| 2 | 문서 drift 회귀 검사 추가 | 이 branch에서 완료 |
| 3 | benchmark/evolve를 `VerificationEvidencePort`로 전환 | 미구현, 최우선 후속 |
| 4 | workspace bundle을 Agent-Verifier request에 연결 | primitive 완료, wiring 미구현 |
| 5 | receipt/request digest와 immutable artifact reference 도입 | 미구현 |
| 6 | 기존 policy key metadata와 artifact top-level schema 검증 | 미구현 |
| 7 | Verifier container sandbox와 service transport 구현 | 미구현 |
| 8 | Control 단독 queue/policy ownership과 config snapshot 구현 | 미구현 |

## 6. Exit Criteria

이 문서를 `완전 일치`로 갱신하려면 다음 조건이 모두 충족되어야 한다.

1. Benchmark와 Evolve가 Agent step projection이 아니라 검증된 Verifier evidence를
   사용한다.
2. Agent와 Verifier 사이에 host path 대신 digest-bound workspace bundle reference가
   전달된다.
3. Verifier가 문서화된 container sandbox 제약으로 실행된다.
4. Queue가 effective config와 policy digest를 submit 시점에 snapshot한다.
5. Receipt와 artifact reference가 post-write tampering을 탐지한다.
6. 전체 unit/branch-coverage/package/offline build 및 container parity 검증이 통과한다.

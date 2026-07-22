# Architecture and Data Pipeline

이 문서는 Sisyphus Harness의 실제 구현을 기준으로 논리적 아키텍처,
신뢰 경계, 런타임 데이터 모델, 영속화 구조, 직접 실행·큐·benchmark·evolution
파이프라인을 하나의 관점으로 정리한다. 현재 구현과 ADR에 기록된 목표 구조는
명시적으로 구분한다. 마지막 코드 대조 결과와 미구현 항목은
[`architecture-conformance-review-2026-07-18.md`](architecture-conformance-review-2026-07-18.md)에
고정한다. 특정 commit을 이 문서의 영구 기준으로 삼지 않으며, runtime code와
회귀 테스트가 동작의 최종 권위다.

현재 구현의 미완료 경계는
[`status/implementation-debt.md`](status/implementation-debt.md)에서 안정 ID와
완료 조건으로 관리하고, 상태 판정에는
[`status/conformance-model.md`](status/conformance-model.md)의 canonical token을
사용한다.

기존 [`architecture.md`](architecture.md)는 짧은 개요이고,
[`evolution.md`](evolution.md)는 evolution 정책만 다룬다. 이 문서는 운영자가
입력한 데이터가 어느 경계를 통과하고, 어떤 검증을 거쳐, 어디에 저장되며,
다음 단계에서 어떻게 소비되는지를 상세히 설명한다.

책임별 탐색과 변경 영향 확인은
[`architecture/README.md`](architecture/README.md)에서 시작한다. 그 구조는 Agent,
Verifier, Evolve, Control의 소유·비소유 권한과 trust/artifact/pipeline 관점을 별도
문서로 나눈다. 이 파일은 기존 링크를 보존하는 상세 호환 설명이며, 새 구조의 목표
상태가 현재 구현되었다는 근거로 사용하지 않는다.

## 1. 시스템 목적과 경계

Sisyphus Harness는 Git 저장소 하나를 대상으로 로컬 coding model을 제한된
파일 도구와 operator verifier 사이에서 실행하는 repository-local control
plane이다. 핵심 설계 목표는 다음 네 권한을 분리하는 것이다.

1. **모델 추론 권한**: 다음 행동을 JSON으로 제안한다.
2. **workspace 변경 권한**: harness가 허용한 여섯 파일 도구만 실행한다.
3. **검증 권한**: operator가 TOML에 정의한 argv만 실행하고 receipt를 만든다.
4. **정책 활성화 권한**: evolution 결과와 별도로 operator 서명 승인을 요구한다.

모델은 shell, 임의 network tool, Git lifecycle, merge, release, queue 전이,
정책 승인·활성화 권한을 갖지 않는다. OpenAI-compatible provider 자체는 HTTP
endpoint다. 기본 direct/queued runtime은 model write를 positive allowlist로 제한하고
immutable bundle을 Docker verifier에 전달한다. 명시적인 `trusted-in-process` 모드와
현재 benchmark/evolution compatibility path는 candidate code를 operator 계정으로
실행하므로 process sandbox가 아니다.

```mermaid
flowchart LR
    Operator["Operator / CLI"]
    Config["TOML config"]
    Repo["Git workspace"]
    Harness["Sisyphus Harness runtime"]
    Model["OpenAI-compatible local model"]
    Bundle["Immutable workspace bundle"]
    Verifier["Contained verifier service"]
    Authority["Git common-dir authority store"]
    Fixtures["Visible fixture + hidden verifier"]
    GEPA["Offline GEPA engine"]

    Operator --> Harness
    Config --> Harness
    Harness <--> Model
    Harness <--> Repo
    Harness --> Bundle
    Bundle --> Verifier
    Harness <--> Authority
    Fixtures --> Harness
    Harness <--> GEPA
```

## 2. 논리적 아키텍처

물리적으로는 `src/sisyphus_harness` 아래의 runtime 모듈과 `contracts`, `ports`,
`adapters`, `infra` 하위 package로 구성된다. 책임은 다음 다섯 논리 계층으로 나뉘며, 이 표는
디렉터리 모양보다 실제 의존 방향을 설명한다.

| 논리 계층 | 모듈 | 책임 |
| --- | --- | --- |
| Interface | `cli.py` | 명령 파싱, 경로 해석, composition 호출, JSON 출력과 exit code |
| Application orchestration | `agent.py`, `agent_loop.py`, `agent_state.py`, `agent_transitions.py`, `agent_artifacts.py`, `worker.py`, `benchmarks.py`, `evolution.py` | Agent 조립과 loop 전이, leased job 실행, 격리 평가, 후보 최적화 및 판정 |
| Contracts and ports | `contracts/`, `ports/`, `config.py`, `protocol.py`, `compaction.py`, `models.py` | versioned wire dataclass, service port, TOML validation, model decision schema, deterministic context reduction, legacy type alias |
| Runtime composition and adapters | `runtime.py`, `adapters/`, `provider.py`, `tools.py`, `verifier.py` | trust-mode 조립, bundle/Docker 또는 explicit in-process 검증, HTTP chat completion, allowlisted workspace 도구 |
| Infrastructure and authority | `infra/`, `authority.py`, `database.py`, `queue.py`, `workspace.py`, `receipts.py`, `run_store.py`, `policy.py` | workspace bundle primitive, Git common-dir authority, SQLite transaction, lease state, path/snapshot, atomic artifact, 승인·활성화 영속화 |

### 2.1 모듈 의존 구조

```mermaid
flowchart TD
    CLI["cli.py"] --> Runtime["runtime.py"]
    CLI --> Worker["worker.py"]
    CLI --> Bench["benchmarks.py"]
    CLI --> Evolve["evolution.py"]
    CLI --> Queue["queue.py"]
    CLI --> Policy["policy.py"]

    Worker --> Runtime
    Worker --> Queue
    Worker --> Policy
    Bench --> AgentFactory["AgentRunFactoryPort"]
    Bench --> Adapter["adapters/in_process.py compatibility"]
    Evolve --> Contracts["contracts/"]
    Evolve --> Provider["provider.py"]
    Evolve --> Receipts["receipts.py"]
    Policy --> Evolve

    Runtime --> Adapter
    Runtime --> BundleAdapter["adapters/bundle_verification.py"]
    BundleAdapter --> Docker["adapters/docker_verifier.py facade"]
    Docker --> DockerRuntime["adapters/docker_runtime.py"]
    Docker --> DockerHost["adapters/docker_host_verification.py"]
    Docker --> DockerEvidence["adapters/docker_evidence.py"]
    Adapter --> Agent["agent.py facade"]
    Adapter --> Verifier["verifier.py trusted path"]
    AgentFactory --> AgentPort["AgentRunPort"]
    Adapter -. implements .-> AgentFactory
    Adapter -. implements .-> AgentPort

    Agent --> AgentLoop["agent_loop.py"]
    Agent --> AgentContext["agent_context.py"]
    Agent --> AgentState["agent_state.py"]
    Agent --> AgentTransitions["agent_transitions.py"]
    Agent --> AgentArtifacts["agent_artifacts.py"]
    AgentLoop --> Provider
    AgentLoop --> Protocol["protocol.py"]
    AgentState --> Compact["compaction.py"]
    AgentTransitions --> Tools["tools.py"]
    AgentTransitions --> VerificationPort["VerificationPort"]
    AgentArtifacts --> RunStore["run_store.py"]

    Queue --> DB["database.py"]
    Queue --> Models["models.py"]
    Verifier --> VerificationContracts["contracts/verification.py"]
    Verifier --> Workspace["workspace.py"]
    Tools --> Workspace
    RunStore --> Receipts
    Verifier --> Receipts
    Policy --> Receipts
```

### 2.2 주요 조립 지점

`cli._main()`은 command별 interface root이고 `runtime.py`가 direct/queued 실행의
trust-mode composition root다. `untrusted-contained`는 operator가 지정한
`execution.writable_paths`가 없으면 시작하지 않으며, `BundleVerificationAdapter`가
매 검증 시점의 workspace를 content-addressed bundle로 만들고
`DockerVerifierTransport`에 보낸다. `trusted-in-process`만
`InProcessVerificationAdapter`와 `BoundedVerifier`를 host에서 조립한다.

큐에서는 `CodingWorker`가 immutable input bundle을 attempt workspace에 materialize한
뒤 같은 `runtime.build_agent_run()`을 호출한다. benchmark는 `AgentRunFactoryPort`를
받지만 현재 기본값은 `InProcessAgentRunFactory`다. evolution은 이 benchmark
evaluator를 호출하므로 coding loop를 복제하지 않지만, benchmark/evolution verifier는
아직 default contained runtime에 편입되지 않았다.

container 분리의 책임, workspace 전달, verification authority, queue와 artifact
소유권은 [`docs/adr`](adr/)에 고정되어 있다. 기존 module import 경로는
호환 alias이지만 새 코드의 wire type은 `contracts`에서 가져오고 concrete
service 대신 `ports`에 의존한다. CLI와 Worker는 `runtime.py`를 통과하고,
benchmark의 compatibility factory만 아직 `adapters/in_process.py`를 직접 사용한다.

### 2.3 현재 구현, 전환 상태, 목표 상태

기본 direct Agent와 queued Worker는 `untrusted-contained` mode를 사용한다. model
write는 operator-owned positive allowlist 안에서만 허용되고, final/intermediate
verification은 현재 candidate tree를 immutable bundle로 캡처한 뒤 host가 조율하는
command별 Docker container에서
실행된다. queued Worker는 추가로 submit 시점의 config digest, effective policy
snapshot과 input bundle을 payload에 고정한다. 각 claim은 Git common-dir 아래의
disposable attempt workspace에 bundle을 materialize하고 ephemeral Git baseline을 만든
뒤 Agent를 실행하며, 원본 repository를 수정하지 않고 output bundle을 게시한다.

`DockerVerifierTransport`는 immutable image identity를 확정하고 실행하는 기본
`VerificationServicePort` facade다. `adapters/docker_bundle_view.py`는 exact CAS view,
`adapters/docker_runtime.py`는 sandbox와 process lifecycle,
`adapters/docker_host_verification.py`는 command observation과 receipt 조립,
`adapters/docker_evidence.py`는 단일 원자 publication을
담당한다. host는 bundle CAS에서 요청한 `.tar`/`.json` 두 파일만
stable-stat/no-symlink/size/SHA/reference 검증을 거쳐 private view로 복사하고 exact
workspace를 materialize한다. 각 `CommandSpec`은 별도 container의 PID 1이며 non-root,
no-network, read-only root filesystem, resource limits를 적용받는다. candidate에는
`/workspace` read-write와 선택된 `/verifier-assets` read-only만 보이고 request, bundle
CAS, evidence staging과 authoritative artifact root는 보이지 않는다. combined Docker
stdout/stderr에는 byte limit을 두고 output overflow/timeout에는 process group과 CID
container를 정리한다.
Control profile은 operator verifier asset tree의 content-addressed reference를
필수로 포함한다. transport는 이 exact tree만 별도로 materialize해 read-only mount하고,
host는 command 실행 전후 tree hash를 확인한다. host는 mutable image reference를
Docker image ID로 resolve하고 request admission 직후 다시 확인한 뒤 exact ID를 실행한다.
candidate stdout은 diagnostic artifact일 뿐 service result protocol이 아니다. 호스트가
stdout/stderr, `CommandResult`, v3 receipt와 v2 service result를 만들고
size/SHA/parser/request/bundle/profile/image/asset/command binding을 재검증한 뒤에만
authoritative artifact root로 원자 게시한다. `BundleVerifierService`와
`VerificationExecutorPort`는 legacy/standalone compatibility surface로 남아 있지만 기본
Agent/Control 조립에서는 호출되지 않는다. Compose는 이 host-owned command loop와 view
생성기를 포함하지 않으므로
operator가 `SISYPHUS_BUNDLE_STORE`를 exact validated request pair만 든 새 directory로
준비해야 한다. 전체 harness config를 사용하는 `agent-run`, `worker-once`, `verify`는
이 contained 경로를 기본으로 사용한다. verification-only legacy config는
`--trusted-in-process`를 명시해야 한다.

Control은 SQLite의 current `AttemptFinished`를 다시 읽고 exact output bundle을 독립
검증한다. authoritative receipt artifact를 다시 읽은 뒤 typed observation과
immutable `EvidenceContract`를 3값으로 평가하며, job/attempt/digest/bundle/contract/
profile/request/receipt/image/asset/evaluation binding을 fenced publication 경계에서 재검사한 후
`TaskOutcome`을 한 번만 기록한다. queue의 `completed`는 실행 종료이고
`TaskOutcome.passed`만 의미적 성공이다. outcome은 contract, profile,
resolved execution identity, `ContractEvaluation` snapshot도 포함한다. 별도의 SQLite knowledge index는
candidate-only search/dependency/next-step projection을 제공하지만 source span admission,
Claim/Gap/TaskBasis authority와 dispatch는 구현하지 않았다.

따라서 다음은 여전히 accepted ADR의 **목표 상태**다.

- 독립 Agent, Evolve, Control 배포와 authenticated transport
- admitted source/span/Claim/Gap/TaskBasis/TaskGraph authority store와 outbox
- evidence-contract registry와 Gap closure transaction/lifecycle
- remote authenticated Control transport와 external append-only outcome ledger
- retention, revocation, schema migration과 multi-tenant artifact service

### 2.4 CLI 진입점과 pipeline

| 명령 | 시작하는 동작 | 주요 영속화 |
| --- | --- | --- |
| `init` | authority DB schema 초기화 | `authority.sqlite3` |
| `verifier-assets-create` | operator verifier script/fixture tree를 immutable CAS로 freeze | verifier asset bundle store |
| `verification-profile-create` | 선택된 command와 verifier asset reference를 v2 profile로 결박 | stdout JSON |
| `queue-enqueue` | 임의 kind/payload의 low-level enqueue | SQLite job row |
| `queue-claim` | low-level lease claim/reclaim | job status와 lease |
| `queue-heartbeat` | 현재 owner의 lease 연장 | lease expiry |
| `queue-finish` | low-level completed/failed 전이 | terminal `result_json` |
| `queue-get` | job 조회 | 없음 |
| `task-submit` | typed `coding-agent` payload enqueue | SQLite job row |
| `worker-once` | job 하나 claim, agent 실행, execution terminal 전이 | queue + immutable `attempt_finished` + artifacts |
| `task-status` | execution/attempt/outcome projection 조회 | 없음 |
| `task-adjudicate` | current attempt를 contained verifier로 검증하고 contract 판정 | immutable `task_outcomes` + verification artifacts |
| `verify` | 선택된 operator command 실행 | verification artifacts |
| `agent-run` | 직접 coding loop 실행 | agent + verification artifacts |
| `benchmark-run` | dataset 전체의 격리 rollout 평가 | rollout artifacts; aggregate는 stdout |
| `evolve` | baseline, GEPA, candidate 재평가 | evolution + rollout artifacts |
| `policy-approve` | proposed candidate에 operator 승인 | signed approval JSON |
| `policy-activate` | 승인과 candidate를 검증해 active 설정 | signed `active.json` |
| `policy-show` | active signature 검증 및 candidate 출력 | 없음 |
| `graph-init`, `graph-put-*` | candidate-only knowledge index 초기화와 수동 projection 입력 | `knowledge-index.sqlite3` |
| `graph-search`, `graph-dependencies`, `graph-next` | revision-bound retrieval/dependency/next-step projection | 없음 |

argument parsing을 통과한 runtime 명령은 stdout 또는 stderr에 JSON을 출력한다.
표준 `argparse --help`와 usage error는 plain text/SystemExit 계약을 유지한다. 정상
완료는 0, 정상적으로 실행됐지만 agent·verification·benchmark·evolution 결과가
gate를 통과하지 못한 경우는 1, configuration·runtime exception은 구조화된 error와
exit 2를 사용한다.

## 3. 핵심 데이터 계약

| 데이터 | 구현 타입 또는 schema | 생성자 | 주요 소비자 |
| --- | --- | --- | --- |
| Harness 설정 | `HarnessConfig` | `load_harness_config()` | CLI, worker, agent, evolution |
| 작업 요청 | `AgentTask` | CLI 또는 `CodingJobPayload` | `LocalCodingAgent.run()` |
| 모델 메시지 | `ChatMessage` | agent 또는 GEPA adapter | `ChatProvider.complete()` |
| 모델 응답 | `ChatResponse` | provider | agent protocol parser 또는 GEPA reflection adapter |
| 모델 행동 | `AgentDecision` | `parse_agent_decision()` | workspace tool 또는 final verifier |
| 도구 결과 | `ToolOutcome` | `WorkspaceTools.execute()` | agent event와 다음 prompt |
| workspace 상태 | `WorkspaceSnapshot` | `snapshot_workspace()` | mutation 대조, stagnation, receipt |
| workspace bundle reference | `WorkspaceBundleRef` | `FilesystemWorkspaceBundleStore` | task-submit, isolated Worker, bundle verifier |
| verifier asset reference | `VerifierAssetBundleRef` | `FilesystemVerifierAssetBundleStore` | v2 profile, verifier mount, Control outcome |
| verifier execution identity | `VerifierExecutionIdentity` | host Docker transport | v2 request/result, v3 receipt, v2 outcome |
| 검증 명세 | `CommandSpec` | TOML parser 또는 benchmark case | `BoundedVerifier` |
| 검증 결과 | digest-bound `VerificationReceipt` + `ArtifactRef` | `BoundedVerifier.verify()` | agent feedback, benchmark authoritative scoring, Control final adjudication |
| agent 결과 | `AgentResult` | `AgentRunRecorder.finish()` | CLI, attempt diagnostic, benchmark scorer |
| 실행 완료 | `AttemptFinished` | current leased Worker | Control, status projection |
| 완료 조건 | `EvidenceContract`, selector/clause/predicate AST | operator/Control | pure evaluator와 Control adjudication |
| 검증 관찰/판정 | `EvidenceObservation`, `ContractEvaluation` | trusted adapter/evaluator | Control; Agent success와 독립 |
| 의미적 결과 | `TaskOutcome` | Control-only service | status projection, future task lifecycle |
| derived graph | `KnowledgeNode`, `KnowledgeEdge`, `NextStepContext` | manual input + knowledge index | retrieval/planning candidate only |
| queue row | `JobRecord` | `JobQueue` | CLI, worker |
| 정책 후보 | `CandidatePolicy` | config 또는 GEPA | benchmark, approval, active run |
| 단일 평가 | `EvaluationObservation` | benchmark evaluator | GEPA와 aggregate |
| 평가 집계 | `EvaluationAggregate` | evolution evaluator | baseline/candidate 비교 |
| evolution 결과 | `EvolutionResult` | `EvolutionRunner` | operator approval |

경계가 고정된 입력 schema인 TOML config, typed coding job, benchmark dataset/case,
model decision, candidate policy, workspace bundle reference는 unknown field를
거부한다. 반면 low-level queue payload는 의도적으로 임의 object를 허용하고,
OpenAI-compatible response envelope는 필요한 field만 읽으며 추가 field를 허용한다.
policy approval/active artifact의 최상위 object도 현재는 signature와 필수 binding을
검사하지만 unknown field를 거부하지 않는다. config 숫자는 finite 여부와 범위를
검사하고, agent criterion은 선택된 verifier command의 criterion 문자열과 정확히
일치해야 한다. 이 문자열 binding이 task 성공과 무관한 검증 명령이 성공을
부여하는 것을 막는다.

## 4. Authority와 저장 구조

authority root는 현재 worktree의 `.git` 경로가 아니라 다음 명령의 결과를
기준으로 계산한다.

```text
git rev-parse --git-common-dir
```

따라서 linked worktree들은 동일한 queue, 정책, artifact 저장소를 공유한다.
authority path가 common directory 밖으로 resolve되면 초기화가 실패한다.

```text
<git-common-dir>/sisyphus-harness/
├── authority.sqlite3
├── knowledge-index.sqlite3
├── attempts/
│   └── <job-id>/attempt-NNNN/workspace/  # 실행 뒤 제거
├── artifacts/
│   ├── agent/
│   │   └── <run-id>/
│   │       ├── metadata.json
│   │       ├── steps/0001.json
│   │       ├── compactions/0001.json
│   │       └── result.json
│   ├── verification/
│   │   └── <verification-run-id>/
│   │       ├── receipt.json
│   │       └── 00-<command>/
│   │           ├── stdout.txt
│   │           └── stderr.txt
│   ├── workspace-bundles/
│   │   ├── <archive-sha256>.tar
│   │   └── <archive-sha256>.json
│   ├── verifier-assets/
│   │   ├── <manifest-sha256>.json
│   │   └── <manifest-sha256>/
│   │       ├── manifest.json
│   │       └── files/
│   └── evolution/
│       ├── <evolution-id>/
│       │   ├── baseline.json
│       │   ├── candidate.json
│       │   ├── result.json
│       │   └── gepa/
│       ├── <evolution-id>-rollouts/
│       └── benchmark-rollouts/
└── policies/
    ├── authority.key
    ├── approvals/<evolution-id>-<hash-prefix>.json
    └── active.json
```

JSON과 text receipt는 같은 디렉터리에 임시 파일을 쓰고 `fsync`, `os.replace`,
directory `fsync` 순서로 원자 저장된다. run ID와 evolution ID는 제한된 문자
집합으로 검증되고 기존 run directory를 덮어쓰지 않는다.

### 4.1 SQLite job schema

`authority.sqlite3`의 `jobs` row는 다음 정보를 가진다.

| 필드 | 의미 |
| --- | --- |
| `job_id` | 서버가 생성한 UUID 기반 식별자 |
| `idempotency_key` | 요청 중복 방지용 unique key |
| `kind` | worker dispatch 종류, 현재 typed worker는 `coding-agent`만 지원 |
| `payload_json` | canonical JSON으로 저장된 요청 payload |
| `status` | `queued`, `running`, `completed`, `failed` |
| `lease_owner`, `lease_expires_at` | running 상태의 현재 소유자와 만료 epoch |
| `attempts` | claim 또는 expired lease reclaim 횟수 |
| `result_json` | completed/failed terminal 결과 |
| `created_at`, `updated_at` | UTC ISO-8601 audit 시간 |

SQLite는 WAL, foreign key, 30초 busy timeout을 사용한다. 쓰기 transaction은
`BEGIN IMMEDIATE`로 시작해 claim과 terminal transition의 경쟁을 직렬화한다.

schema version 2는 두 append-only projection을 추가한다. `attempt_finished`는
current Worker lease와 attempt 번호를 확인하는 terminal transaction에서만 생성된다.
`task_outcomes`는 해당 attempt의 digest와 composite foreign key를 가지며 Control이
독립 검증을 마친 뒤에만 생성된다. 두 table 모두 UPDATE/DELETE trigger가 변경을
거부한다. low-level `queue-finish`는 `jobs.result_json`만 쓸 수 있어 Control outcome의
입력이 될 수 없다.

## 5. 설정과 정책 해석 파이프라인

모든 주요 실행은 먼저 repository-contained TOML을 `HarnessConfig`로 변환한다.

```mermaid
flowchart LR
    TOML["sisyphus-harness.toml"] --> Parse["strict TOML parser"]
    Parse --> ProviderSettings
    Parse --> AgentLimits
    Parse --> CadencePolicy
    Parse --> StrategyPrompt
    Parse --> VerificationConfig
    Parse --> EvolutionSettings
    Source{"policy source"}
    StrategyPrompt --> Source
    CadencePolicy --> Source
    Active["signed policies/active.json"] --> Source
    Source --> Candidate["CandidatePolicy used by run"]
```

- `--policy config`: TOML의 strategy와 cadence를 `CandidatePolicy`로 감싼다.
- `--policy active`: `active.json`의 HMAC을 검증하고 candidate를 복원한다.
- safety prompt, tool schema, limits, verifier command는 active policy가 바꾸지
  못한다.
- direct run은 config path를 해당 run의 model write-protected path로 등록한다.

큐의 `task-submit`은 repository-relative config path와 `config|active` 선택뿐 아니라
config SHA-256, resolved `CandidatePolicy` snapshot, content-addressed input
`WorkspaceBundleRef`를 함께 저장한다. Worker는 materialized config digest를 다시
검사하고 payload의 policy snapshot을 사용한다. 제출 뒤 원본 repository나 active
policy가 바뀌어도 이미 queued된 attempt의 입력은 조용히 바뀌지 않는다. legacy
low-level queue payload에서 세 snapshot field가 모두 없을 때만 Worker가 claim 시점에
호환 bundle/policy를 만든다.

## 6. 직접 agent 실행 파이프라인

### 6.1 시작 전 검증

`agent-run`은 다음 순서로 시작된다.

1. repo와 config 경로를 resolve하고 containment를 검사한다.
2. config 또는 signed active policy를 선택한다.
3. task와 중복 없는 acceptance criteria를 `AgentTask`로 만든다.
4. 모든 task criterion이 선택된 `CommandSpec.criteria`에 존재하는지 확인한다.
5. Git HEAD, staged/unstaged diff, untracked content로 초기 workspace snapshot을
   만든다.
6. 중복되지 않는 agent artifact directory를 만들고 `metadata.json`을 쓴다.

workspace는 반드시 Git repository와 유효한 HEAD를 가져야 하지만 clean할
필요는 없다. `AgentResult.changed_paths`는 run 이전 대비 delta가 아니라 최종
HEAD 대비 changed path 집합이므로, 시작 전에 존재한 변경도 포함될 수 있다.
초기·최종 `state_hash`를 함께 사용해야 run 중 변화를 정확히 구분할 수 있다.

### 6.2 step loop

```mermaid
sequenceDiagram
    participant CLI
    participant Agent
    participant Snapshot as WorkspaceSnapshot
    participant Model as Local Model
    participant Tools as WorkspaceTools
    participant Verify as BoundedVerifier
    participant Store as AgentRunStore

    CLI->>Agent: AgentTask + CandidatePolicy + CommandSpec[]
    Agent->>Snapshot: initial snapshot
    Agent->>Store: metadata.json
    loop step <= max_steps
        Agent->>Agent: cadence check and optional compaction
        Agent->>Snapshot: before snapshot
        Agent->>Model: safety + context + optional working file
        Model-->>Agent: one JSON decision
        Agent->>Agent: strict parse and stagnation check
        alt tool decision
            Agent->>Tools: execute(tool, arguments)
            Tools-->>Agent: ToolOutcome
            Agent->>Snapshot: after snapshot
            Agent->>Agent: compare reported mutation with state hash
            opt mutation cadence reached
                Agent->>Verify: intermediate verification
                Verify-->>Agent: VerificationReceipt
            end
        else finish decision
            Agent->>Verify: final verification
            Verify-->>Agent: VerificationReceipt
        end
        Agent->>Store: steps/NNNN.json
    end
    Agent->>Store: result.json
    Agent-->>CLI: AgentResult
```

`LocalCodingAgent`는 입력 검증과 객체 조립만 담당한다. `AgentRunLoop`가 step
순서를 고정하고, `AgentRunState`가 counter와 cycle 상태를 보유하며,
`AgentToolTransitionHandler`와 `AgentVerificationTransitionHandler`가 각각
workspace mutation과 verifier 전이를 처리한다. `AgentRunRecorder`만 run artifact
JSON을 투영한다.

각 prompt context에는 task, criterion, strategy, deterministic compact summary,
최근 event, 주기적 workspace observation, 알려진 file hash, criterion status,
reflection flag, cadence가 들어간다. 마지막으로 읽거나 쓴 working file의
내용은 JSON escape로 손상되지 않도록 별도 user message에 최대 4,000자까지
verbatim으로 전달된다.

provider는 `/chat/completions`에 non-streaming request를 보내며 strict JSON
response schema를 요청한다. response body는 16 MiB로 제한되고, 반환된 content는
다시 harness parser와 tool argument validator를 통과한다.

### 6.3 여섯 workspace 도구

| 도구 | 읽기/쓰기 | 핵심 제약 |
| --- | --- | --- |
| `list_files` | 읽기 | Git tracked + non-ignored untracked 파일만, 출력 제한 |
| `read_file` | 읽기 | relative path, UTF-8 text, file byte limit, line 범위 |
| `search_text` | 읽기 | literal substring, 최대 200건, 전체 출력 제한 |
| `write_file` | 쓰기 | 기존 파일은 exact SHA-256, 신규 파일은 hash `null` |
| `replace_text` | 쓰기 | exact SHA-256, old fragment가 정확히 한 번 존재 |
| `delete_file` | 쓰기 | exact SHA-256, regular file만 |

공통 제약은 다음과 같다.

- absolute path와 parent escape를 거부한다.
- `.git`과 `.sisyphus-harness` path component를 거부한다.
- write 경로의 중간 symlink와 symlink target을 거부한다.
- config처럼 run이 지정한 protected path를 읽을 수는 있지만 쓸 수 없다.
- 기존 mode를 보존한 임시 파일과 `os.replace`로 원자 변경한다.
- tool의 `mutated` 값과 before/after workspace hash가 다르면 run을 실패시킨다.

### 6.4 context compaction과 stagnation

compaction은 LLM summarization이 아니라 deterministic reducer다. 오래된 event에서
tool count, 읽은/변경한 파일, 최근 error, 최신 verification을 누적하고 지정된
수의 최근 event는 그대로 유지한다. step interval 또는 serialized context char
limit 중 하나가 충족되면 실행되며 각 결과는 별도 compaction receipt에 남는다.

stagnation은 두 방식으로 차단한다.

- reason text를 제외한 decision fingerprint가 연속 반복되는 경우
- workspace state와 criterion status 조합이 이전 상태로 순환하는 경우

각 threshold는 cadence policy의 `stagnation_limit`을 사용한다.

### 6.5 종료 조건

성공은 model의 `finish`가 아니라 final `VerificationReceipt.passed`로 결정된다.
동일한 workspace state에서 이미 실패한 final verification을 다시 요청하면
검증을 재실행하지 않고 수정을 요구한다. 다음 조건은 fail-closed result를 만든다.

- provider failure 또는 protocol error budget 초과
- runtime 또는 step budget 초과
- repeated decision 또는 workspace cycle stagnation
- tool error가 workspace를 변경한 상태로 종료
- verifier가 workspace를 변경
- final verification 실패 후 예산 소진

하나의 monotonic runtime deadline을 각 step뿐 아니라 built-in provider, tools,
verifier에 전달하고 모든 blocking timeout을 남은 시간으로 줄인다. deadline-aware
protocol을 구현하지 않은 third-party provider/tool adapter는 이미 시작한 blocking
호출을 선점할 수 없으므로 trusted compatibility boundary다.

## 7. Verification 파이프라인

`BoundedVerifier`는 shell string이 아니라 `CommandSpec.argv`를 직접
`subprocess.Popen`에 전달한다. shell metacharacter는 일반 인자로 취급된다.

```mermaid
flowchart TD
    Spec["CommandSpec[]"] --> Baseline["workspace baseline snapshot"]
    Baseline --> Resolve["resolve argv[0] and hash executable"]
    Resolve --> Spawn["spawn process group, no stdin"]
    Spawn --> Output["stream full stdout/stderr to files"]
    Output --> Timeout{"timeout?"}
    Timeout -->|yes| Kill["terminate process group"]
    Timeout -->|no| Exit["capture exit code"]
    Kill --> Final["workspace final snapshot"]
    Exit --> Final
    Final --> Mutation{"workspace unchanged?"}
    Mutation --> Receipt["receipt.json + CommandResult[]"]
```

command별 pass 조건은 launch 성공, timeout 없음, exit code 0, workspace unchanged를
모두 만족하는 것이다. run 전체는 모든 command가 pass하고 run 전후 workspace가
같아야 pass한다. failure는 `launch_error`, `timeout`, `output_limit`,
`process_leak`, `workspace_mutation`, `execution_error`, `assertion_failure`,
`command_failure`로 분류된다.

receipt에는 argv, criterion, duration, exit code, timeout, executable path와
`argv[0]` hash, stdout/stderr 상대경로, command 및 run 전후 workspace hash가
포함된다. script가 `argv[1]`에 있는 경우 그 script hash까지 자동으로 provenance에
포함하지는 않는다.

receipt는 중복되지 않는 run directory에 원자적으로 저장된다. reader는 regular-file,
no-symlink, byte limit, size, SHA-256, stable stat, strict JSON과 run-path binding을 다시
검사한다. 같은 local OS 권한을 가진 사용자가 authority root 자체를 바꾸는 공격에
대한 서명이나 외부 append-only storage는 아직 제공하지 않는다.

`BoundedVerifier`는 explicit `trusted-in-process`, standalone Compose service와
benchmark/evolution compatibility path의 command runner다. operator 계정으로 직접
실행되는 경로 자체는 process sandbox가 아니다. 기본 direct/queued composition의
`DockerVerifierTransport`는 host에서 exact bundle을 materialize하고 command마다 별도
container를 시작한다. candidate container에는 workspace와 선택된 read-only asset만
mount하며 request, 전체 bundle CAS, writable evidence staging, authoritative evidence는
mount하지 않는다. transport output이 합산 byte limit을 넘거나 deadline을 초과하면
container를 제거하고 host가 bounded failure observation을 만든다. command 종료 후 host가
workspace/asset tree를 다시 측정하고 receipt artifact와 run directory를 게시한다.
`/tmp`는 noexec이며 test program은 materialized `/workspace`에서 실행한다.

## 8. Queue와 worker 파이프라인

```mermaid
stateDiagram-v2
    [*] --> queued: enqueue(idempotency_key)
    queued --> running: claim + lease
    running --> running: heartbeat
    running --> running: expired lease reclaimed by another worker
    running --> completed: current owner publishes AttemptFinished
    running --> failed: execution cannot publish an attempt
    completed --> [*]
    failed --> [*]
```

### 8.1 제출

`task-submit`은 다음 payload를 canonical JSON으로 저장한다.

```json
{
  "task": "coding instruction",
  "criteria": ["verbatim criterion"],
  "config": "repository-relative-config.toml",
  "policy": "config|active",
  "run_id": "optional-safe-run-prefix",
  "workspace_bundle": {"bundle_id": "workspace:sha256:..."},
  "config_sha256": "sha256:...",
  "policy_snapshot": {"candidate_hash": "sha256:..."}
}
```

동일 idempotency key와 동일 kind/payload는 기존 job을 반환한다. 같은 key에 다른
요청을 넣으면 conflict다.

worker는 retry artifact 충돌을 막기 위해 이 prefix 뒤에
`-attempt-<four-digit-attempt>`를 붙인다. prefix가 없으면 immutable queue job ID를
사용한다.

### 8.2 claim과 실행

worker는 가장 오래된 queued job 또는 만료된 running job 하나를 transaction
안에서 claim한다. `LeaseKeeper` thread는 lease 시간의 1/3 주기로 heartbeat한다.
Worker는 input bundle을 Git common-dir의 실행별 새 디렉터리에 materialize하고
deterministic Git baseline을 만든다. config digest와 policy snapshot을 확인한 뒤
`runtime.build_agent_run()`으로 trust mode, write allowlist와 verifier adapter를 조립해
Agent loop를 구동한다. 결과 tree를 별도 output bundle로 게시한 뒤 attempt workspace를
제거한다.

Agent loop가 output bundle과 strict `AttemptFinished`를 게시하면 Agent의 diagnostic
success 값과 무관하게 `completed`로 전이한다. payload/config/attempt 실행 자체가
output lineage를 만들지 못한 exception은 구조화된 `failed` result로 저장한다.
terminal update는 현재 owner이고 lease가 유효하며 attempt 번호가 current일 때만
성공한다. 이후 Control만 독립 verification과 contract evaluation으로 별도
`TaskOutcome`을 게시한다.

lease는 SQLite row 전이를 fence한다. 만료된 attempt를 강제로 중단시키지는 않지만,
attempt 번호별 workspace와 artifact ID가 분리되어 replacement Worker나 operator의
source repository를 함께 수정하지 않는다. stale attempt의 terminal update는 lease
조건에서 거부된다. 같은 input에서 여러 output bundle이 생성될 수 있으므로 향후
TaskGraph promotion은 현재 dispatch/evaluation binding을 다시 확인해야 한다.

## 9. Benchmark 데이터 파이프라인

benchmark dataset은 case directory 목록을 담고, 각 `case.json`은 instruction,
criterion, visible workspace, criterion별 hidden verifier script를 정의한다.
verifier 순서와 criterion 순서는 정확히 일대일로 일치해야 한다.

```mermaid
flowchart LR
    Dataset["dataset JSON"] --> Cases["strict case loader"]
    Cases --> Copy["copy visible workspace"]
    Copy --> Git["deterministic fresh Git commit"]
    Git --> Agent["AgentRunPort rollout"]
    Hidden["external hidden verifier scripts"] --> Agent
    Agent --> VerifyArtifacts["verification artifacts"]
    Agent --> Trace["agent step/result artifacts"]
    VerifyArtifacts --> Reader["digest/size/strict receipt reader"]
    Reader --> Binding["exact command/workspace binding"]
    Binding --> Score["criterion and efficiency scoring"]
    Trace --> Score
    Score --> Obs["EvaluationObservation"]
    Obs --> Aggregate["EvaluationAggregate"]
```

평가마다 새 rollout directory를 만들고 visible workspace만 복사한다. `.git`,
`.sisyphus-harness`, bytecode cache는 제외하며 고정 author/committer timestamp로
초기 commit을 만든다. hidden verifier는 복사된 workspace 밖에 남지만 command
cwd는 rollout workspace이므로 그 workspace의 코드를 검사한다.

점수는 다음 식을 사용한다.

```text
score = 0.70 * criterion_pass_rate
      + 0.15 * success
      + 0.10 * step_efficiency
      + 0.05 * compaction_efficiency
```

- `criterion_pass_rate`: authoritative final receipt의 command criterion pass 비율
- `success`: Agent success와 final receipt pass가 모두 참인지 여부
- `step_efficiency`: max step 대비 적게 사용한 정도
- `compaction_efficiency`: max compaction 대비 적게 사용한 정도

scorer는 `AgentResult.verification_artifacts`의 마지막 reference를 filesystem evidence
reader로 다시 읽어 size/SHA-256/strict schema를 검증한다. receipt의 workspace/final
state뿐 아니라 command name/argv/criteria 전체와 순서를 원래 benchmark request에
exact match한다. Agent step projection은 trace 진단에만 쓰고 correctness 권위로
사용하지 않는다.

verifier mutation이나 tool mutation mismatch는 hard-gate failure다. evaluator
exception은 score 0, success false, hard gate false인 observation으로 변환되어
나머지 case 평가를 계속할 수 있다. `benchmark-run` aggregate는 stdout JSON으로
출력되고, 각 rollout의 상세 receipt는 authority artifact에 남는다.

## 10. GEPA evolution 파이프라인

evolution이 변경할 수 있는 surface는 `strategy_prompt`와 schema-valid
`CadencePolicy`뿐이다. safety prompt, tools, path checks, limits, verifier, queue,
approval gate는 후보 데이터에 포함되지 않는다.

```mermaid
flowchart TD
    Seed["seed CandidatePolicy"]
    Train["train dataset"]
    Holdout["disjoint holdout dataset"]
    Seed --> BaseTrain["baseline train evaluation"]
    Train --> BaseTrain
    Seed --> BaseHold["baseline holdout evaluation"]
    Holdout --> BaseHold
    BaseTrain --> BaselineFile["baseline.json"]
    BaseHold --> BaselineFile
    Train --> GEPA["GEPA optimize_anything"]
    Seed --> GEPA
    GEPA --> Candidate["candidate strategy + cadence"]
    Candidate --> ReTrain["independent train rerun"]
    Candidate --> ReHold["independent holdout rerun"]
    Train --> ReTrain
    Holdout --> ReHold
    ReTrain --> Gate["delta, success, hard-gate checks"]
    ReHold --> Gate
    Gate -->|pass| Proposed["proposed result"]
    Gate -->|fail| Rejected["rejected result"]
    Proposed --> Files["candidate.json + result.json"]
    Rejected --> Files
```

train과 holdout은 non-empty이고 각 set 안의 ID가 unique하며 서로 disjoint여야 한다.
baseline train/holdout을 먼저 기록한 다음 GEPA는 trainset을 optimization dataset과
GEPA valset으로 사용한다. GEPA evaluator는 rollout score와 함께 trace summary,
criterion 결과, success, hard gate, sub-score를 reflection side information으로
제공한다.

GEPA가 선택한 candidate는 engine 점수만 신뢰하지 않고 train과 holdout 전체에서
다시 실행된다. 다음을 모두 만족해야 `proposed`가 된다.

- train mean score가 baseline보다 `min_train_delta` 이상 개선
- holdout mean score가 baseline보다 `min_holdout_delta` 이상 개선
- holdout success rate가 baseline보다 낮지 않음
- 기본 설정에서는 모든 holdout case 성공
- train과 holdout의 모든 hard gate 통과

그 외에는 reasons를 가진 `rejected` 결과다. `proposed`도 아직 active policy가
아니다.

## 11. 승인과 활성화 파이프라인

```mermaid
sequenceDiagram
    participant Operator
    participant Result as Evolution result.json
    participant Registry as PolicyRegistry
    participant Key as authority.key
    participant Active as active.json

    Operator->>Registry: policy-approve(evolution-id, note)
    Registry->>Result: require accepted + proposed
    Registry->>Registry: recompute candidate hash
    Registry->>Key: load existing key or create 32-byte key with mode 0600
    Registry-->>Operator: signed approval receipt
    Operator->>Registry: policy-activate(result, approval)
    Registry->>Registry: verify HMAC, evolution ID, candidate hash
    Registry->>Active: atomically write signed active policy
    Registry-->>Operator: activated
```

approval은 candidate hash, evolution ID, username, UID, hostname, note, timestamp를
HMAC-SHA256으로 묶는다. activation은 승인 signature와 candidate binding을 다시
검사하고 active document 전체에 새 signature를 붙인다. active load도 signature를
검사하므로 파일 tampering은 실패한다.

이 HMAC은 같은 local authority root 안의 무결성 경계다. 외부 KMS, hardware key,
조직 identity attestation을 제공하지 않으므로 외부 신원 증명으로 해석하면 안 된다.
새 key는 mode `0600`으로 생성되지만 기존 `authority.key`의 길이와 mode는 현재
재검증하지 않는다. approval/active 최상위 schema 역시 unknown field를 거부하지
않으며, HMAC은 해당 추가 field를 포함한 전체 payload의 변조만 탐지한다.

## 12. End-to-end 데이터 lineage

| 단계 | 입력 | 변환/검사 | 출력 | 영속화 위치 | 다음 소비자 |
| --- | --- | --- | --- | --- | --- |
| Config load | TOML | unknown field, type, finite/range validation | `HarnessConfig` | 없음 | CLI/worker |
| Policy resolve | config 또는 signed active file | candidate schema/hash/HMAC | `CandidatePolicy` | active file은 `policies/` | agent/benchmark/evolution |
| Task submit | task, criteria, config/policy, workspace | strict payload, config digest, effective policy와 input bundle snapshot, idempotency | `JobRecord` | SQLite `jobs` + workspace bundle CAS | worker |
| Agent prompt | task, policy, events, snapshots | cadence, compaction, observation | `ChatMessage[]` | step receipt에 복제 | provider |
| Model completion | OpenAI-compatible JSON | 16 MiB ceiling, JSON decision parse | `AgentDecision` | raw response in step receipt | tools/verifier |
| Tool execution | decision arguments | containment, hash, size, symlink, atomic write | `ToolOutcome` | step receipt | next prompt |
| Workspace bundle | current Git tree | tracked/unignored enumeration, stable entry read, tar path/type/digest validation | `WorkspaceBundleRef` | authority workspace bundle store | Worker/Verifier/Control |
| Verification | profile + exact bundle/workspace | deadline, process lifecycle, exit, mutation, request/profile binding | `VerificationReceipt` + `ArtifactRef` | verification artifacts | agent feedback, benchmark, Control |
| Attempt finish | source/output bundle + AgentResult diagnostic | lease owner, expiry, current attempt, content digest | `AttemptFinished` | SQLite append-only record | Control |
| Evidence adjudication | current attempt, profile, immutable contract | independent verify, authoritative artifact re-read, typed normalization, 3-value evaluation, fenced publish | observations + `ContractEvaluation` + `TaskOutcome` | verification artifacts + SQLite outcome | status/future lifecycle |
| Knowledge projection | candidate nodes/edges | integrity preflight, revision-bound bounded traversal and scoring | `NextStepContext` | `knowledge-index.sqlite3` | planning only |
| Agent finish | initial/final snapshot, receipts | success/failure rule | `AgentResult` | agent `result.json` | CLI/queue/scorer |
| Benchmark | case + candidate | isolated rollout, score formula | `EvaluationObservation` | rollout artifacts | aggregate/GEPA |
| Evolution | seed, train, holdout | baseline, GEPA, independent rerun, gates | `EvolutionResult` | evolution artifacts | operator approval |
| Approval | proposed result | candidate hash + HMAC | approval receipt | `policies/approvals` | activation |
| Activation | result + approval | HMAC and binding verification | active policy | `policies/active.json` | later run |

## 13. 무결성과 일관성 제어

| 위험 | 구현 제어 |
| --- | --- |
| repository 밖 path 접근 | `Path.resolve()` 후 root-relative containment |
| symlink write escape | 중간 symlink 및 target symlink 거부 |
| stale model write | 현재 file SHA-256을 mutation argument로 요구 |
| 부분 파일/receipt | same-directory temp, fsync, atomic replace |
| tool의 거짓 mutation 보고 | workspace state hash before/after 대조 |
| verifier가 코드 변경 | command별 및 run 전체 snapshot 대조 |
| 무관한 verifier로 task 성공 | task criterion과 command criterion exact binding |
| 중복 queue 요청 | unique idempotency key + canonical payload 대조 |
| stale worker terminal write | owner, unexpired lease, current attempt 조건부 update와 attempt digest |
| stale Worker filesystem collision | immutable input bundle + attempt-number isolated workspace |
| verifier artifact substitution | ArtifactRef size/SHA/strict parse/run binding과 inline equality 재검증 |
| task success self-assertion | Agent Boolean 없는 AttemptFinished + Control-only output-bundle verification/EvidenceContract/TaskOutcome |
| graph candidate의 authority 승격 | 모든 projection에 `derived_candidate_only`; dispatch port 없음 |
| malformed model output | strict response schema, parser, protocol error budget |
| 반복/진동 | decision fingerprint와 state/criterion cycle limit |
| evolution의 안전 제어 약화 | candidate surface를 strategy/cadence로 제한 |
| 승인 후 candidate 변조 | candidate content hash와 HMAC binding |

현재 표의 제어는 local process와 filesystem 신뢰 경계 안에서만 성립한다. receipt
reader는 읽는 순간의 byte integrity를 검증하지만 같은 OS 계정이 authority root를
임의 변경하는 공격을 외부 서명이나 append-only store로 막지는 않는다.

`WorkspaceSnapshot.state_hash`는 HEAD commit, unstaged binary diff, staged binary
diff, untracked path와 file hash 또는 symlink target을 순서대로 SHA-256에 넣는다.
이 값은 semantic equivalence가 아니라 byte-level repository state identity다.

## 14. 관측성과 replay 자료

agent step receipt에는 full prompt messages, raw model response, parsed decision,
event, token count, duration, workspace before/after가 들어간다. 따라서 실행 판단을
사후 추적할 수 있지만 source content와 model output이 포함될 수 있으므로 artifact를
외부 공유하기 전 redaction이 필요하다.

verification stdout/stderr는 잘리지 않은 파일로 저장되고 agent에 되돌려주는
feedback은 category 중심으로 제한된다. benchmark diagnostics에는 action-oriented
trace summary와 rollout path가 포함되어 GEPA reflection이 실패 패턴을 사용할 수
있다.

완전한 byte-for-byte replay를 보장하는 event-sourcing 시스템은 아니다. 특히
provider model binary/hash와 전체 server 설정은 기본 agent metadata에 자동 기록되지
않는다. queue는 config digest와 effective policy/input bundle을 snapshot하지만 TOML
bytes 자체와 model binary/server image attestation까지 한 manifest로 묶지는 않는다.
재현성 주장이 필요한 실행은 repository의 evidence manifest처럼 model hash, server
args, config bytes, container image digest와 run artifacts를 별도로 묶어야 한다.

## 15. 성능 특성과 병목

- `snapshot_workspace()`는 대부분의 agent step 전후에 Git diff와 untracked scan을
  수행하므로 repository 크기와 변경량에 비례하는 orchestration 비용이 생긴다.
- `search_text`는 대상 UTF-8 파일을 Python에서 순차 scan하므로 총 repository byte에
  비례한다. index 또는 `rg` backend는 사용하지 않는다.
- full prompt와 raw response를 매 step JSON에 저장하므로 긴 run의 artifact 용량이
  빠르게 증가할 수 있다.
- artifact retention과 SQLite job pruning 정책은 구현되어 있지 않다.
- GEPA는 `parallel=False`이며 각 candidate/example가 실제 agent rollout을 수행하므로
  모델 latency가 전체 evolution 시간을 지배한다.

## 16. 운영상 명시적 한계

현재 구현의 안전한 적용 범위는 supervised, single-user 실험이다.

1. 기본 direct/queued verifier는 Docker containment를 요구하지만 multi-tenant 또는
   secret-oracle 격리를 주장하지 않는다. explicit `trusted-in-process`와 현재
   benchmark/evolution compatibility verifier는 sandbox가 아니다.
2. POSIX `BoundedVerifier` compatibility path는 same-process-group descendants와 열린
   output pipe를 정리하지만, 새 session/process group으로 이동하고 모든 pipe를 닫은
   descendant는 관찰할 수 없다. 기본 Docker path에서는 command가 container PID 1이고
   PID 1 종료와 함께 namespace 전체가 종료된다.
3. generic third-party provider/tool port는 global deadline을 선점할 수 없다. built-in
   provider, tools와 verifier에는 남은 deadline이 전달된다.
4. Control profile에 포함된 verifier asset tree와 Docker image는 hash로 고정되지만,
   asset bundle 밖의 임의 script argument나 전체 dependency graph는 자동 수집하지 않는다.
5. local HMAC은 외부 identity 또는 non-repudiation을 제공하지 않는다.
6. artifact retention, 대형 repository snapshot/Graph batch-ingestion 최적화가 없다.
7. GraphRAG는 candidate-only이며 exact EvidenceSpan, Claim/Gap/TaskBasis admission과
   split/replace/weaken task evolution은 아직 계획 상태다.
8. 기본 Docker command는 evidence namespace와 분리됐지만 mounted verifier asset
   bytes는 읽을 수 있다. standalone Compose verifier service와 test command는 여전히
   한 UID/mount namespace를 공유한다. hidden profile/oracle confidentiality에는 candidate가
   bytes를 받지 않는 별도 evaluator가 필요하다.

이 제약은 [`SECURITY.md`](../SECURITY.md)와
[`code-review-2026-07-21.md`](code-review-2026-07-21.md)의 release constraint와
동일하다.

## 17. 변경 영향 탐색표

| 변경하려는 동작 | 먼저 볼 코드 | 관련 회귀 테스트 |
| --- | --- | --- |
| CLI 명령 또는 JSON exit contract | `cli.py` | `tests/test_cli.py` |
| trust mode, service 조립과 dependency direction | `runtime.py`, `adapters/in_process.py`, `ports/` | `tests/test_runtime.py`, `tests/test_in_process_adapters.py`, `tests/test_architecture_dependencies.py` |
| task/criterion와 loop 종료 | `agent.py`, `agent_loop.py`, `agent_state.py`, `agent_transitions.py`, `agent_artifacts.py` | `tests/test_agent.py`, `tests/test_architecture_dependencies.py` |
| model JSON schema | `protocol.py`, `provider.py` | `tests/test_protocol.py`, `tests/test_provider.py` |
| file boundary와 mutation | `tools.py`, `workspace.py` | `tests/test_tools.py`, `tests/test_workspace.py` |
| workspace bundle 생성/materialization | `infra/workspace_bundle.py`, `contracts/workspace.py` | `tests/test_workspace_bundle.py` |
| command 실행과 receipt | `verifier.py`, `contracts/verification.py` | `tests/test_verifier.py` |
| bundle verifier, asset CAS와 container transport | `services/verifier.py`, `infra/verifier_assets.py`, `adapters/docker_verifier.py`, `adapters/docker_bundle_view.py`, `adapters/docker_runtime.py`, `adapters/docker_host_verification.py`, `adapters/docker_evidence.py` | `tests/test_verifier_service.py`, `tests/test_verifier_assets.py`, `tests/test_docker_verifier.py`, `tests/test_docker_verifier_host.py` |
| evidence 조건식과 final adjudication | `contracts/evidence_contract.py`, `evidence_contract.py`, `services/evidence_contract.py`, `services/control_outcomes.py` | `tests/test_evidence_*` |
| Control outcome authority | `contracts/control/`, `ports/control_outcomes.py`, `infra/control_outcomes.py` | `tests/test_control_outcomes.py` |
| candidate-only graph retrieval | `contracts/knowledge.py`, `knowledge_graph.py`, `infra/knowledge_index.py` | `tests/test_knowledge_*` |
| queue transition과 concurrency | `database.py`, `queue.py`, `worker.py` | `tests/test_database.py`, `tests/test_queue.py`, `tests/test_worker.py` |
| benchmark fixture와 scoring | `benchmarks.py` | `tests/test_benchmarks.py` |
| candidate schema와 acceptance gate | `evolution.py` | `tests/test_evolution.py`, `tests/test_gepa_integration.py` |
| approval 및 active policy | `policy.py` | `tests/test_policy.py` |
| atomic artifact layout | `receipts.py`, `run_store.py` | agent/verifier/evolution 통합 테스트 |

## 18. 요약

Sisyphus Harness의 중심 데이터 흐름은 `operator intent → strict config/task →
bounded model decision → contained workspace mutation → operator verification →
atomically persisted receipt`이다. queue는 이 흐름 앞뒤에 transactional lease를 추가하고,
benchmark는 이를 격리 fixture에서 점수화하며, evolution은 실제 rollout
observation으로 strategy와 cadence만 개선한다. 개선 후보는 independent holdout을
통과해도 자동 배포되지 않고 local operator HMAC approval을 거쳐야 active가 된다.

이 구조에서 신뢰의 최종 기준은 model의 설명이 아니라 immutable input/output bundle,
request/profile-bound receipt artifact, EvidenceContract evaluation, queue transaction,
candidate hash와 approval signature다. knowledge graph의 점수와 next-step 결과는
검색·계획 후보일 뿐 이 권위 체인에 들어오지 않는다. Task/Gap의 실제 lifecycle
authority와 source-span admission은 `task-graph-evolution-plan.md`의 후속 단계다.

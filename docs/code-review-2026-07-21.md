# Code Review - 2026-07-21

## 결론

이번 변경은 **근거 중심 task/knowledge graph의 foundation vertical slice**로서
supervised experimental 범위에 머지할 수 있다. 현재 GraphRAG는 명시적으로
`derived_candidate_only`이며 Claim, Gap, TaskBasis, queue 상태를 직접 바꾸지 않는다.
따라서 이 결론은 자동 task authority나 production multi-tenant 실행이 완성됐다는
뜻이 아니다.

리뷰는 authority/storage, queue/worker, workspace bundle, provider, model tools,
agent loop, verifier lifecycle, evidence contract/evaluator, benchmark/evolution,
candidate knowledge graph, Docker transport, CLI, packaging, CI, security 및 architecture
문서를 대상으로 했다. 현재 주장 범위 안의 P0/P1 구현 결함은 수정했다. 더 넓은
production 범위에는 아래의 명시적 P1 경계가 남는다.

## 해결한 Findings

### CR-H1: Control이 inline receipt와 reference만 신뢰할 수 있었다

final adjudication이 전달받은 `VerificationServiceResult.receipt`를 직접 사용하면,
artifact reference가 존재해도 저장된 bytes와 다른 객체로 판정할 여지가 있었다.

해결: `VerificationServicePort.read_receipt()`를 추가했다. Control은 artifact를 다시
읽어 regular-file/no-symlink/size/SHA-256/strict-schema/run-path를 검증하고 inline
receipt와 exact equality를 확인한 뒤 그 authoritative receipt만 observation으로
변환한다. 누락, digest mismatch, parsed-content mismatch 회귀를 추가했다.

### CR-H2: stateful subclass가 Evidence 판정을 바꿀 수 있었다

frozen dataclass의 subclass가 내부 field를 변경하면 이미 lint된 `AtLeast` 식의
minimum을 낮춰 같은 evidence를 `FAIL`에서 `PASS`로 바꿀 수 있었다.

해결: evaluator가 권위 있게 사용하는 contract/clause/expression/observation과
verification request/result 경계에 exact model 및 scalar/collection type을 요구한다.
evaluator version을 올리고 type-mismatch `NOT_EQUALS`/`DISJOINT`도
`INDETERMINATE`로 fail closed한다. 일부 legacy 외곽 contract의 문자열 validator는
아래 CR-O6에 별도 범위로 남긴다.

### CR-H3: 손상된 knowledge index에 write 일부가 commit될 수 있었다

projection integrity 확인이 mutation 뒤에 일어나면 기존 손상을 발견하면서 새
node/edge write는 남을 수 있었다.

해결: node/edge/provenance의 exact model과 scalar type을 ingress에서 검사하고 canonical
round-trip한 projection만 쓴다. `BEGIN IMMEDIATE` transaction 안에서 integrity
preflight와 write 후 postflight를 모두 수행하므로 기존 손상뿐 아니라 trigger가 만든
node/edge/term 손상도 전체 rollback한다. 초기화도 기존 손상 metadata를 덮어쓰지 않고,
dependency cycle 확인과 insert 역시 같은 transaction이다.

### CR-H4: Docker verifier가 authoritative evidence root를 쓸 수 있었다

container가 최종 artifact root를 read-write mount하면 compromised verifier가 기존
receipt를 변경하거나 임의 run을 게시할 수 있었다.

해결: 매 실행마다 비어 있는 sibling staging root만 mount한다. host가 request,
bundle, profile, run binding과 staged receipt bytes를 검증한 뒤 O_EXCL publish lock과
`os.replace`로 한 run directory만 게시하고 다시 읽어 검증한다.

### CR-H5: verifier leader 종료 뒤 자식 process가 살아남을 수 있었다

leader가 성공한 뒤 같은 process group의 자식이나 inherited output pipe가 남으면
receipt 발행 뒤 workspace를 변경할 수 있었다.

해결: POSIX process group 생존과 pipe EOF를 bounded selector loop로 확인하고 잔존
group을 종료한다. pipe가 grace period를 넘으면 `process_leak`으로 실패한다. thread
fallback도 reader가 남으면 fail closed한다.

### CR-H6: Docker container가 다른 task의 workspace bundle CAS를 열람할 수 있었다

전체 read-only bundle store mount는 변조를 막지만 같은 store의 다른 source archive와
reference를 container command가 열람하는 것은 막지 못했다.

해결: host가 요청 digest의 `.tar`/`.json` 한 쌍만 fresh per-run view로 복사한다. 원본
directory/file을 no-follow로 열고 open 전후 inode/stat 안정성, regular-file type, archive
size/SHA-256 및 strict reference equality를 확인한다. container에는 이 view만 read-only
mount하므로 다른 task의 CAS object는 보이지 않는다.

### CR-H7: Docker CLI output을 host memory에 무제한 수집할 수 있었다

`subprocess.run(capture_output=True)`는 compromised service가 내보내는 stdout/stderr를
host memory에 제한 없이 축적할 수 있었다.

해결: 두 pipe를 하나의 byte budget으로 selector-drain한다. 합산 한도를 넘거나 wall
timeout이 발생하면 Docker client process group을 즉시 종료하고 cidfile의 container를
`docker rm --force`한 뒤 artifact를 게시하지 않는다.

### CR-M1: candidate ranking이 후보 수에 대해 superlinear하게 증가했다

candidate별 node/edge 재조회와 전체 term table scan이 `graph-next` 비용을 키웠다.

해결: revision을 시작/종료에서 한 번씩 확인하고 node/edge cache를 공유한다. term
query는 candidate ID로 제한하고 SQLite parameter를 900개 단위로 chunk한다. candidate,
dependency, traversal 총량에 hard bound를 둔다.

### CR-M2: benchmark가 요청과 다른 passing command를 점수화할 수 있었다

receipt 안의 일부 명령만 통과해도 요청한 verification profile과 다른 명령을 선택할
가능성이 있었다.

해결: 최종 receipt의 command name, argv, criteria와 순서를 요청 profile과 exact
비교한 뒤 criterion score를 계산한다.

### CR-M3: provider credential이 redirect 요청에 전달될 수 있었다

일반 `Authorization` header는 redirect handler가 새 origin으로 전달할 수 있다.

해결: credential은 `Request.add_unredirected_header()`로 설정한다. provider URL도
HTTP(S), hostname, credential/query/fragment 금지와 port parsing을 선행 검증한다.

### CR-M4: Docker transport timeout이 container를 orphan으로 남길 수 있었다

host의 `docker run` process timeout만으로 실제 container 종료가 보장되지 않았다.

해결: 실행별 `--cidfile`을 사용하고 timeout/OSError 시 엄격한 hex container ID만
읽어 bounded `docker rm --force`를 수행한다. 실제 timeout 스모크에서 새 orphan이
없음을 확인했다.

### CR-M5: Docker Desktop에서 `/work`가 암묵적으로 noexec였다

`noexec`를 생략하는 것만으로 executable tmpfs가 보장되지 않았다. 실제 bundle의
실행 script가 mode `0755`인데도 `EACCES`로 실패했다.

해결: Docker CLI와 Compose의 `/work` mount에 `exec`를 명시했다. 실제 container에서
content-addressed bundle의 executable script가 통과하고 workspace가 불변임을
검증했다. `/tmp`는 계속 `noexec`다.

### CR-M6: model이 Git-ignored 경로에 변경을 숨길 수 있었다

Git 기반 snapshot과 workspace bundle은 ignored file을 포함하지 않으므로 model tool이
그 경로에 쓰면 상태/evidence 밖의 side effect가 될 수 있었다.

해결: tracked file이 아닌 Git-ignored write/delete target을 거부한다. model은 root와
nested `.gitignore` 자체를 write/replace/delete할 수 없어 임시 unignore 후 side effect를
숨길 수도 없다. `./`, `../`, backslash 등 lexical alias도 canonical path validation
전에 거부하고 subprocess timeout을 `ToolError`로 정규화한다.

### CR-M7: architecture 문서가 이전 runtime을 설명했다

queue snapshot, isolated attempt workspace, receipt scoring, verification service,
candidate graph와 global deadline 설명 일부가 실제 코드보다 오래됐다.

해결: current/transitional/target 상태를 분리하고 bundle lineage, authoritative receipt
reader, Docker staging/publish, EvidenceContract, candidate-only GraphRAG 및 남은 authority
layer를 문서와 architecture regression test에 반영했다.

### CR-M8: queued artifact run ID가 호출자 correlation을 잃었다

queue 경로가 전달받은 `run_id`를 무시하고 내부 job ID만 artifact prefix로 사용해 외부
correlation과 retry lineage가 조용히 달라질 수 있었다.

해결: payload의 optional run prefix를 길이/문자 집합으로 검증하고, 각 artifact ID를
`<run-or-job>-attempt-NNNN`으로 결정한다. unsafe prefix와 retry suffix 회귀를 추가했다.

### CR-M9: CI가 정적 보안 검사와 container build를 실행하지 않았다

로컬에서 통과한 Ruff/Bandit/Docker 검증이 GitHub merge gate에는 없었다.

해결: frozen Python 3.14 환경에서 Ruff와 Bandit medium/high를 실행하고 Docker image
build 및 Compose config를 검증하는 `static-and-container` job을 추가했다.

## 남은 위험과 후속 Gate

### CR-O1 (P1): candidate graph에는 grounding admission authority가 없다

현재 candidate task node는 exact `EvidenceSpan -> admitted Claim -> Gap -> TaskBasis`를
요구하지 않는다. `supports`와 `contradicts`도 source quality, exact offset, body coverage
또는 contradiction semantics를 판정하지 않는다. 따라서 graph 결과를 dispatch 또는
Gap closure에 사용하면 안 된다.

필수 후속: structural source/chunk/span contract, coverage receipt, Claim/TaskBasis
admission store와 transaction을 구현하고 이 gate를 통과한 task만 별도 authoritative
task graph에 materialize한다.

### CR-O2 (P1): Docker가 기본 final authority는 아니다

기본 CLI composition의 in-process verifier는 operator 계정 권한으로 실행된다. 같은
process group과 열린 pipe는 감지하지만 새 session으로 이탈하고 모든 inherited pipe를
닫은 process는 host verifier가 관찰할 수 없다.

필수 후속: untrusted 실행에서는 Docker/cgroup/PID namespace 또는 동등한 OS containment를
필수 composition으로 만들고 in-process adapter는 trusted compatibility 경로로 제한한다.

### CR-O3 (P1): queue 완료와 Evidence 기반 TaskOutcome이 아직 분리되어 있다

Subsequent status: superseded by
[`ADR 0006`](adr/0006-control-owned-task-outcomes.md) and the
[`Slice B implementation`](reviews/2026-07-21/stage-b-control-authority.md).
This paragraph remains the finding as observed at the reviewed revision.

Worker는 현재 `AgentResult` 성공값으로 queue의 execution-terminal `COMPLETED`를 쓴다.
Control adjudication은 shadow이며 상태 전이를 하지 않는다. `CodingJobResult`에도
TaskSpec/dispatch/latest-attempt/contract digest가 모두 들어 있지 않아, 이 서비스를
그대로 권위 경로로 노출하면 다른 contract나 stale output을 결합할 여지가 있다.

필수 후속: queue terminal과 semantic TaskOutcome을 별도 상태로 유지하고,
task/spec/dispatch/job/attempt/source-output bundle/contract/plan digest를 authoritative
store에서 조회·fencing한 Control-issued final run만 outcome을 만들게 해야 한다.

### CR-O4 (P2): source ingestion과 task evolution은 아직 계획 상태다

typed `SourceDocumentVersion`, `StructuralChunk`, exact `EvidenceSpan`, ontology/versioned
entity-relation extraction, body/abstract coverage, split/replace/weaken/tombstone lifecycle은
아직 구현되지 않았다. 이번 graph는 수동 JSON candidate projection이다.

### CR-O5 (P2): Docker evidence는 환경 attestation이나 hidden oracle을 증명하지 않는다

receipt는 request/bundle/profile/command executable에 bind되지만 image digest, base image,
OS package, environment allowlist와 verifier asset dependency graph를 모두 attest하지
않는다. service와 test command도 같은 UID/mount namespace에서 request/profile/staging을
볼 수 있다. 이는 host containment이지 secret oracle 또는 multi-tenant confidentiality
경계가 아니며, staging tmpfs/disk quota도 command별로 분리되지 않는다.

### CR-O6 (P2): freshness와 일부 독립 contract strictness가 완성되지 않았다

observation에는 evaluation-time TTL/freshness model이 아직 없어 temporal stale을 판정할
수 없다. `EvidenceAdjudicationRequest`와 verification/command 외곽의 일부 문자열
validator도 `isinstance(str)`를 사용한다. JSON/기본 composition은 plain built-in을
생성해 확인된 runtime 우회는 없지만 internal untrusted caller 경계까지 exact scalar를
보장하지는 않는다. 독립 `KnowledgeProjection`도 `matched_terms`가 query의 부분집합인지는
검사하되 실제 교집합을 모두 포함했는지는 스스로 증명하지 않는다. 현재 generator는
전체 교집합을 계산하지만 projection 단독 검증을 authority로 사용하면 안 된다.

### CR-O7 (P2): graph bulk ingestion은 여전히 비싸다

각 write의 전체 integrity scan은 개별 write 안전성을 높이지만 대량 ingestion에서
quadratic 비용이 될 수 있다. CLI도 최대 1 MiB JSON argv를 직접 받으며 versioned
file/stdin/CAS envelope와 schema migration/rebuild manifest가 없다.

### CR-O8 (P2): artifact retention과 verifier provenance가 제한적이다

agent/verification/bundle/benchmark artifact에 age/size quota와 prune policy가 없다.
일반 command provenance는 `argv[0]`을 hash하지만 script argument, imported module,
hidden oracle asset 전체를 자동으로 bind하지 않는다.

### CR-O9 (P2): deadline-aware protocol은 모든 third-party adapter에 강제되지 않는다

built-in provider, tools, verifier는 하나의 monotonic deadline을 전달받는다. generic
third-party provider/tool port는 이미 시작한 blocking 호출을 선점할 수 없으므로 별도
process containment 또는 deadline-aware protocol이 필요하다.

### CR-O10 (P2): 정적 typing gate가 없다

Pyright는 56개 source file에서 105개 error를 보고했다. 대부분 strict JSON의 `object`
narrowing과 tagged-union 식별을 type checker가 추론하지 못하는 영역이지만, CI gate가
없어 실제 type regression과 구분되지 않는다.

권장 후속: parser helper에 `TypeGuard`/typed decoder를 도입하고 expression dispatch를
명시적으로 narrow한 뒤, baseline을 0으로 줄여 CI에 추가한다.

### CR-O11 (P3): 큰 모듈과 복잡한 validator를 분할해야 한다

전체 670개 block의 평균 cyclomatic complexity는 A(4.37)지만
`CommandResult.__post_init__`, receipt observation binding, verifier lifecycle,
workspace bundle extraction과 EvidenceContract parser에는 C-E block이 있다.
contract가 안정된 뒤 parsing, invariant validation, execution lifecycle을 작은 pure
function으로 분리하는 편이 안전하다.

### CR-O12 (P3): local HMAC은 외부 identity가 아니다

approval key와 authority artifact가 같은 OS account 아래 있다. model tool과 우발적
수정은 막지만 account compromise에 대한 non-repudiation은 제공하지 않는다.

### CR-O13 (P3): supply-chain 재현성은 digest-pinned가 아니다

GitHub Actions는 release tag, verifier image는 mutable base tag를 사용하고 sdist에는
tests가 포함된다. 현재 supervised release의 기능 gate는 통과하지만 고보안 배포에서는
action commit SHA와 base image digest를 pin하고 dependency provenance/SBOM 및 최소 sdist
manifest를 별도 정책으로 검증해야 한다.

## Architecture Conformance

| 영역 | 판정 | 근거 |
| --- | --- | --- |
| Control/Worker/Agent/Verifier 권한 분리 | 부분 구현 | exact output bundle과 service port는 연결됨; task authority store는 미구현 |
| Agent-neutral adjudication | shadow foundation 충족 | immutable contract, typed observations, tri-state pure evaluation, exact receipt reread; TaskOutcome 전이는 미구현 |
| Queue filesystem fencing | 충족 | submit-time bundle과 attempt별 materialization; terminal DB lease와 별도 lineage |
| Untrusted verification containment | opt-in 충족 | Docker request-only bundle view, network/root/capability/resource/output/staging boundary; 기본값은 trusted in-process |
| Candidate GraphRAG | 충족 | immutable projection, digest/integrity/cycle/bounded traversal, candidate-only authority |
| Evidence-grounded task admission | 미구현 | exact source spans, Claim/Gap/TaskBasis authority가 다음 phase |
| Residual-Gap task evolution | 미구현 | split/replace/weaken/closure transaction이 다음 phase |
| Benchmark scoring authority | 충족 | digest-read receipt와 exact requested command/profile binding |
| Documentation | 충족 | current/transitional/target와 명시적 release limits 동기화 |

## 검증 기록

| Gate | 결과 |
| --- | --- |
| Python 3.11 exact CI suite + branch coverage | 356 tests, 1 skipped; 91.110611% (6,637 statements, 2,250 branches) |
| Python 3.14 exact CI suite + branch coverage | 356 tests, 1 skipped; 91.110611%; pytest view 355 passed, 1 skipped, 597 subtests |
| Evolution optional dependency test | Python 3.14 isolated environment, 1 passed |
| Ruff | 통과 |
| Bandit medium/high | 0건; prevalidated HTTP open, Docker tmpfs literal, parameter-placeholder SQL 등에 총 5개 명시적 false-positive suppression |
| compileall / lock / manifest / diff check | 통과; evidence manifest 126 files |
| offline sdist/wheel + Python 3.14 isolated install/CLI | 통과 |
| Docker image / Compose config | 통과 |
| Docker executable bundle / receipt reread | PASS, workspace unchanged |
| Docker host timeout cleanup | fail closed, 새 orphan container 0 |
| Docker request-only CAS view / outer output limit | decoy 4개 중 exact pair 2개만 노출; 256-byte overflow 0.323초 fail closed, run 미게시, 새 orphan container 0 |
| Radon | 682 blocks, average A (4.44) |
| Pyright (informational, not CI gate) | 56 files, 120 errors; 후속 typing debt |

## 머지 Gate

이 변경은 다음 조건에서만 머지한다.

1. exact GitHub CI의 Python 3.11/3.14 core, evolution, package/evidence,
   static-and-container job이 모두 통과한다.
2. PR이 current `main`과 충돌하지 않으며 base diff에 `.planning/` 또는 local runtime
   artifact가 포함되지 않는다.
3. GraphRAG를 task/Claim authority로 설명하거나 사용하는 코드가 없다.
4. Docker verifier를 사용하지 않는 경로가 sandbox라고 주장하지 않는다.
5. 머지 뒤 `origin/main`의 tree에서 core regression과 evidence manifest를 다시 확인한다.

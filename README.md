# NextStop STT

NextStop STT는 실제 지하철 녹음을 RTZR Streaming STT로 재생하고, final 응답에서
현재 역명을 추출하는 Python CLI 데모입니다. 전체 전사문을 그대로 보여주는 것보다
`안내방송 인식 → 역명 추출 → 근거 출력` 과정을 재현 가능하게 만드는 데 집중했습니다.

| 평가 대상 | 확인할 질문 | 지표 |
| --- | --- | --- |
| 음성인식 | 안내방송과 역명을 정확히 전사했는가? | CER, 역명 CER, 역명 일치율 |
| 역명 추출 | 현재 역명을 정확한 토큰으로 찾았는가? | 역명 일치율, 추출 근거 |
| 실시간성 | 안내방송을 final 응답으로 언제 확정했는가? | partial/final 수, 응답 시각 |

> **현재 상태:** RTZR 인증, M4A 실시간 재생, Streaming WebSocket, Batch 비교,
> 역명 추출, 비공개 결과 저장과 offline test를 구현했습니다. 아래 수치는 동일한 실제
> 20초 구간을 반복 실행한 관찰값이며 전체 노선 성능으로 일반화하지 않습니다.

## 5분 실행

필요한 도구:

- Python 3.11 (`.python-version`으로 지정)
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- FFmpeg: M4A를 raw LINEAR16 frame으로 변환

```bash
git clone https://github.com/bumfercar/StationAlert.git
cd StationAlert
uv sync --extra dev
uv run nextstop --help
uv run pytest
uv run ruff check .
```

`.env.example`을 복사하고 RTZR credential을 입력합니다. `.env`를 자동으로 읽지는 않으므로
실행 shell에 환경변수를 설정해야 합니다.

```bash
export RTZR_CLIENT_ID="..."
export RTZR_CLIENT_SECRET="..."
uv run nextstop check-auth
```

Windows PowerShell에서는 다음처럼 설정합니다.

```text
$env:RTZR_CLIENT_ID="..."
$env:RTZR_CLIENT_SECRET="..."
uv run nextstop check-auth
```

## 운영체제별 한 번 실행

`.env.example`을 `.env`로 복사하고 credential을 입력한 뒤 실행합니다. 스크립트가
프로젝트 위치 이동, `.env` 로드, frozen dependency 설치, 대화형 CLI 실행을 담당합니다.

| 운영체제 | 실행 명령 |
| --- | --- |
| macOS | `./scripts/run_demo.sh` |
| Linux | `./scripts/run_demo.sh` |
| Windows PowerShell | `powershell -ExecutionPolicy Bypass -File .\scripts\run_demo.ps1` |

CLI는 다음 순서로 질문합니다.

```text
NextStop STT | 지하철 현재역 인식 및 하차 안내
녹음 파일 경로를 입력해주세요:
하차하실 역명을 정확히 입력해주세요:
RTZR Streaming 인식을 시작할까요? [y/N]:
```

파일 길이는 FFprobe로 자동 확인하며 기본적으로 녹음 전체를 실시간 속도로 재생합니다.
`--start-seconds`와 `--duration-seconds`는 특정 실패 구간을 빠르게 다시 확인할 때만 쓰는
고급 option입니다.

현재 비공개 녹음을 처음부터 끝까지 실제 여행처럼 실행하려면 다음처럼 인자를 전달합니다.

```bash
./scripts/run_demo.sh --source-file ../subwayaudio.m4a --destination 어린이대공원 --yes
```

RTZR가 실제로 반환하는 한국어·영어 final 전사를 함께 확인하려면 `--show-text`를
추가합니다. 주변 승객 음성이 포함될 수 있으므로 사용자가 명시했을 때만 출력합니다.

```bash
./scripts/run_demo.sh --source-file ../subwayaudio.m4a --destination 공릉 --show-text --yes
```

원본이 19분 39초이므로 실제 실행도 같은 시간이 걸립니다. 목적지역 부근 UI만 20초 동안
빠르게 확인하려면 고급 구간 option을 사용합니다.

```bash
./scripts/run_demo.sh --source-file ../subwayaudio.m4a --destination 어린이대공원 --start-seconds 1124 --duration-seconds 20 --yes
```

Windows에서도 같은 option을 `run_demo.ps1` 뒤에 붙이면 됩니다.

실제 end-to-end 검증에서는 다음 흐름이 출력됐습니다.

```text
[연결] RTZR Streaming STT 연결 및 실시간 재생 시작
[인식 노선] 공릉 → 태릉입구 → 먹골 → 중화 → 상봉 → 면목 → 사가정 → 용마산 → 중곡 → 군자 → 어린이대공원
[역 인식 기록] 새 역이 확인되면 아래에 한 줄씩 추가됩니다.
[조작] Ctrl+C: 중단하고 그때까지 받은 RTZR 응답 저장
◜ 이동 중 · 첫 역 방송 대기 · RTZR p/f=2/1 · 후보=0 · 00:03 / 00:20 · 원본 18:47 · Ctrl+C 중단
01. 어린이대공원역 | 원본 18:48 | 목적지 도착 · 하차
◝ 이동 중 · 현재 어린이대공원역 · 00:14 / 00:20 · 원본 18:58
[완료] 인식 역=1개 | RTZR partial=12, final=5 | 보류 후보=1개
[근거 저장] results/private/journey-demo.json
```

기본 decoder는 지하철 원거리 방송에서 가장 잘 동작한 `sommers_ko + MEETING`입니다.
노선 전체 역명은 score 1.0으로 유지하고, 사용자가 입력한 목적지역의 본역명과 부역명은
score 2.0으로 올립니다. 따라서 목적지를 우선하면서도 지나가는 역 인식은 계속 수행합니다.

역명 없는 final 응답은 매번 출력하지 않고 JSON에만 보존합니다. 화면의 한 줄 spinner는
음성이 계속 전송 중임을 보여주며, 기존 역과 다른 역이 확정될 때만 번호가 붙은 행을
추가합니다. 두 역이 순서대로 잡히면 노선 순서로 이동 방향을 판별합니다.

한 정거장 전 역을 강한 패턴으로 인식하면 `[하차 준비]`를 출력하도록 구현했습니다.
현재 녹음의 군자 추정 구간에서는 RTZR가 역명을 검출하지 못해 실제 `[하차 준비]`는
발생하지 않았고, 이 한계는 그대로 남겼습니다. 목적지역 자체는 실제 녹음에서
`[도착]`까지 확인했습니다.

## 현재역 추출 상세 명령

직접 녹음했거나 사용 권한이 있는 파일을 지정합니다. `duration-ms`를 필수로 두어 실수로
긴 유료 호출을 실행하지 않도록 했습니다.

```bash
uv run nextstop stream-file --source-file path/to/recording.m4a --start-ms 1124000 --duration-ms 20000 --domain MEETING --model sommers_ko --line7-keyword-score 1.0 --detect-station --output-file results/private/demo-current-station.json
```

실제 확인한 출력은 다음과 같습니다. 기본값에서는 승객 음성이 포함될 수 있는 전체
전사문을 출력하지 않고, 역명과 판단 근거만 보여줍니다.

```text
CURRENT_STATION: 어린이대공원 reason=canonical_with_alias_and_station_suffix seq=2
Streaming completed: partial=12, final=5, stations=1, candidates=1, alerts=0
```

`어린이대공원 세종대 역`처럼 본역명과 부역명 사이에 `역`이 오거나, STT가 마지막
`역`만 놓친 `어린이대공원 세종대`도 알려진 부역명 조합이므로 정답으로
처리합니다. `--line7-keyword-score`는 정답 역 하나가 아니라 녹음 구간의 모든 역명과
부역명을 같은 점수로 등록합니다. 반면 `어린이비복원` 같은 유사 문자열은 fuzzy
match하지 않습니다. 이 실행에서는 `중화` bare token도 1건 나왔지만 `역` 근거가 없어
현재역으로 출력하지 않고 검토 후보로만 저장했습니다.

## 왜 Streaming STT인가

현재역 안내는 전체 녹음이 끝난 뒤 전사하는 것보다 방송이 끝나는 시점에 역명을
확정하는 것이 중요합니다. 그래서 기본 실행 경로는 RTZR 일반 STT가 아니라 Streaming
STT입니다.

일반 STT는 동일한 음성 구간을 파일 단위로 비교할 때 참고 결과로 사용할 수 있지만,
실시간 알림 지연시간을 평가하는 기준으로 사용하지 않습니다.

## 도메인과 keyword score 선택

대상은 지하철 객실에서 스마트폰으로 녹음한 원거리 안내방송입니다. 공식 문서상
`CALL`은 근접 마이크, `MEETING`은 원거리·공개 장소에 적합합니다. 문서 설명만으로
결정하지 않고 동일한 어린이대공원 주변 20초에 한 요소씩 바꿔 실제 응답을 비교했습니다.

| Domain | Keyword score | partial/final | 목표역 final | 관찰 |
| --- | ---: | ---: | --- | --- |
| `CALL` | 없음 | 1/1 | 빈 문자열 | 원거리 안내방송을 거의 검출하지 못함 |
| `MEETING` | 없음 | 12/5 | `어린이비복원 세동제 역` | 방송 구간은 분리했지만 역명 오인식 |
| `MEETING` | 1.0 | 12/5 | `어린이대공원 세종대 역` | 정확히 복원 |
| `MEETING` | 2.0 | 12/5 | `어린이대공원 세종대 역` | 정확하지만 1.0보다 이점이 확인되지 않음 |
| `MEETING` | 3.0 | 12/5 | `어린이대공원 세종대` | 뒤 구간에도 `세종`이 나타나 과한 편향 징후 |

따라서 데모는 `MEETING`, 일반 노선 역명 1.0, 사용자 목적지역 2.0을 사용합니다. 1.0과
2.0이 모두 목표역을 복원했고 3.0부터 편향 징후가 관찰됐으므로, 목적지에만 검증 범위
안의 2.0을 적용하고 나머지 역은 1.0으로 유지했습니다. 이 혼합 설정이 전체 노선의
최적값이라고 주장하지 않으며, 목적지 우선순위를 반영한 제품 정책으로 구분합니다.

## 오디오 전처리와 문맥 기반 역명 복원

객실 저주파 진동과 원거리 방송을 분리하기 위해 같은 먹골 20초 구간에 FFmpeg
전처리를 적용했습니다. 전처리 외 설정은 `sommers_ko + MEETING`, 노선 score 1.0,
목적지 score 2.0으로 고정했습니다.

| 전처리 | partial/final | RTZR의 먹골 유사 출력 | exact 역명 | 문맥 복원 |
| --- | ---: | --- | ---: | ---: |
| `none` | 11/5 | `막고니` | 0 | 미적용 |
| `subway_speech_v1` | 11/2 | `마보니` | 0 | 미적용 |
| `subway_rumble_cut_v1` | 11/3 | `마콜` | 0 | 미적용 |
| `subway_rumble_cut_v1` | 11/3 | `마콜` | 0 | 먹골 1건 |

`subway_speech_v1`은 100 Hz high-pass, 7.5 kHz low-pass, 약한 FFT denoise와 speech
normalization을 함께 적용했지만 final 경계를 5개에서 2개로 크게 바꿨고 exact 역명은
늘지 않았습니다. 반면 저주파만 줄이는 `subway_rumble_cut_v1`은 `마콜`까지 가까워졌지만
전처리만으로는 정답이 아닙니다. 따라서 기본값은 비교 기준인 `none`으로 유지합니다.

`--recover`는 다음 조건을 모두 만족할 때만 `마콜 → 먹골`처럼 역명을 복원하는 opt-in
기능입니다.

- final 안에 `이번…` 같은 안내방송 문맥이 있음
- 후보가 공릉–어린이대공원 구간 역명으로 한정됨
- 한글 음소 edit가 2개 이하이고 정규화 거리가 0.333 이하임
- 두 번째로 가까운 역과의 거리 차이가 0.2 이상임

전사 원문은 수정하지 않습니다. CLI와 비공개 JSON에 `contextual_phonetic_recovery`, 원래
token, 복원한 역명, 음소 거리를 함께 남깁니다. 따라서 이 결과는 **STT exact 성공이
아니라 지하철 도메인 후처리의 역명 추출 성공**으로 평가합니다.

```bash
./scripts/run_demo.sh --source-file ../subwayaudio.m4a --destination 먹골 \
  --start-seconds 265 --duration-seconds 20 \
  --preprocess subway_rumble_cut_v1 --recover --show-text --yes
```

```text
01. 먹골역 | 원본 04:27 | 목적지 도착 · 하차 | 문맥 복원 마콜→먹골 (음소거리 0.333)
```

## 평가 기준

평가 단위를 다음처럼 분리합니다.

- 전사 평가는 사람이 작성한 안내방송 한 구간을 단위로 합니다.
- 역명 평가는 `역` 접미사가 없는 정확한 역명 토큰도 인식 성공으로 봅니다.
- 현재역 근거는 `역명+역`, `역명+부역명(+역)`과 출입문 방향 문맥이 함께 나온 정확한
  역명 토큰처럼 설명 가능한 강한 패턴을 사용합니다.
- 목적지 알림 평가는 별도 확장 기능이며 현재역 인식 성공과 혼동하지 않습니다.
- `TP`, `FP`, `FN`, `TN`을 먼저 저장한 뒤 Precision, Recall, F1을 계산합니다.
- 정답이 없는 구간, 승객 대화, 비슷한 역명도 음성에 포함해 오탐을 확인합니다.
- 실패한 결과도 삭제하지 않고 설정과 원본 응답을 함께 보관합니다.

## 구현 구조

```text
비공개 M4A 또는 공개 샘플
  -> FFmpeg로 mono LINEAR16 PCM 변환
  -> 실제 재생 시간에 맞춘 raw audio frame
  -> RTZR Streaming STT WebSocket
  -> partial/final 응답 및 시간 정보 저장
  -> 한국어 텍스트 정규화
  -> final 응답만 역명 추출
  -> 본역명·부역명 패턴 판정
  -> 현재역과 machine-readable reason 출력
```

RTZR 연결과 역명 추출을 분리했습니다. 저장된 응답으로 추출 규칙을 offline test할 수
있고, STT 인식 오류와 도메인 규칙 오류를 따로 설명할 수 있습니다.

## M4A 입력 처리

보유한 7호선 녹음은 M4A이므로 입력 파일로 사용할 수 있습니다. 다만 M4A 파일 자체를
Streaming API에 보내지는 않습니다.

FFmpeg adapter가 실행 중 다음 조건의 raw PCM을 생성합니다.

- mono
- signed 16-bit little-endian PCM
- RTZR에 명시적으로 전달한 sample rate
- WAV의 `RIFF` header가 없는 raw frame

선택한 전처리 preset은 FFmpeg filter graph와 함께 결과 JSON에 저장됩니다. 전처리를
사용하지 않은 baseline도 같은 구조로 `preprocess=none`을 기록하므로 A/B 조건을 나중에
확인할 수 있습니다.

첫 frame이 `RIFF`로 시작하면 전송을 거부하며, frame 전송 속도는 실제 음성 시간에
맞춥니다. Python 코드는 shell 문자열을 조립하지 않고 FFmpeg 인자를 직접 전달합니다.

## 비밀정보와 음성 파일 보호

`.env.example`을 참고해 로컬 `.env`를 만들거나 환경변수를 직접 설정합니다.

```bash
export RTZR_CLIENT_ID="..."
export RTZR_CLIENT_SECRET="..."
```

다음 파일은 Git에 포함하지 않습니다.

- 실제 `.env`와 인증 토큰
- `private_audio/` 아래의 원본 녹음
- `results/private/` 아래의 비공개 실험 결과
- `reports/private/` 아래의 제출용 보고서
- 승객 음성, 개인 정보, 로컬 절대경로

## 관련 문서

- [프로젝트 및 실험 설계](docs/PROJECT_SPEC.md)
- [Git 작업 방식](docs/GIT_WORKFLOW.md)
- [AI 활용 및 시행착오](docs/ai-workflow.md)

## 현재 한계와 다음 검증

- 현재역 데모는 실제 녹음 범위인 공릉부터 어린이대공원까지의 7호선 역 목록만
  사용하며, 범위 밖 목적지는 API 호출 전에 거부합니다.
- score 비교는 어린이대공원 20초 한 구간의 관찰이므로 더 많은 역에서 재검증해야 합니다.
- 광고가 포함된 긴 구간과 알아듣기 어려운 사람 정답은 CER에서 명시적으로 제외합니다.
- 기본 정책은 fuzzy match를 사용하지 않습니다. `--recover`를 명시한 실험에서만 안내
  문맥, 노선 후보, 음소거리와 차순위 margin을 모두 검사하며 전체 구간 평가는 남아 있습니다.
- 원본 녹음과 전사 결과는 개인정보 보호를 위해 공개 저장소에 포함하지 않습니다.

## 참고한 공식 문서

- [RTZR 인증](https://developers.rtzr.ai/docs/authentications/)
- [RTZR Streaming STT](https://developers.rtzr.ai/docs/stt-streaming/)
- [RTZR Streaming WebSocket](https://developers.rtzr.ai/docs/stt-streaming/websocket/)
- [RTZR 일반 STT](https://developers.rtzr.ai/docs/stt-file/)

# hf2ms

[English](README.md) | **简体中文**

将 Hugging Face 模型或数据集流式传输到 ModelScope。大文件内容不落盘，支持文件级恢复、自动创建目标仓库和增量更新。使用 Python 3.9+，提供 Linux/macOS/Windows 通用的纯 Python wheel。

## 安装与快速开始

```bash
uv sync
uv run hf2ms org/source user/destination --ms-token "$MODELSCOPE_API_TOKEN"
```

`--ms-token` **必须显式传入**，需要目标写入/建仓库权限。即使环境中有 `MODELSCOPE_API_TOKEN`，也需要像上例一样传给参数。`--hf-token` 可选，默认读取 `HF_TOKEN`；公开源通常不需要，私有/gated 源需要已获授权的 token。

```bash
# 私有或 gated HF 源
uv run hf2ms org/source user/destination \
  --ms-token "$MODELSCOPE_API_TOKEN" --hf-token "$HF_TOKEN"

# 只读预览
uv run hf2ms org/source user/destination \
  --ms-token "$MODELSCOPE_API_TOKEN" --dry-run

# 指定版本及筛选路径
uv run hf2ms org/source user/destination \
  --ms-token "$MODELSCOPE_API_TOKEN" \
  --hf-revision v1.2 --ms-revision release \
  --include '*.json' '*.safetensors' --exclude 'original/*'
```

Windows PowerShell：

```powershell
$env:MODELSCOPE_API_TOKEN = '你的 MS token'
hf2ms org/source user/destination --ms-token $env:MODELSCOPE_API_TOKEN
```

安装 wheel 后可直接使用 console script：

```bash
uv tool install ./dist/hf2ms-0.2.6-py3-none-any.whl
hf2ms --help
# 或安装到已有 Python 环境：
pip install ./dist/hf2ms-0.2.6-py3-none-any.whl
```

HTTPS 需要使用 OpenSSL 1.1.1+ 的 Python；macOS/Xcode 旧系统 Python 可能使用 LibreSSL，建议使用 uv 管理的 Python，例如 `uv sync --python 3.11`。

## 参数

| 参数 | 默认值 / 行为 |
|---|---|
| `SOURCE DESTINATION` | 必填，均为 `owner/repository` |
| `--ms-token TOKEN` | **必填**；不自动读取 MS token 环境变量 |
| `--hf-token TOKEN` | 可选；默认 `HF_TOKEN`，显式参数优先 |
| `--repo-type auto\|model\|dataset` | `auto`：向 HF 查询该仓库 ID 是模型还是数据集，唯一匹配时自动选择；两种都存在时需显式指定 |
| `--hf-revision REVISION` | `main`；支持源分支/tag/commit |
| `--ms-revision BRANCH` | `master`；目标可写分支，不自动复制 HF revision |
| `--ms-visibility auto\|private\|public` | `auto`；仅创建仓库时使用 |
| `--on-conflict overwrite\|error\|skip` | `overwrite`；同路径不同/无法确认内容的处理方式 |
| `--include PATTERN [PATTERN ...]` | 包含 glob，可重复 |
| `--exclude PATTERN [PATTERN ...]` | 排除 glob，可重复，优先于 include |
| `--cache-dir PATH` | `~/.cache/hf2ms/`，支持 `~` 展开 |
| `--cache-max-size SIZE` | `256MiB`，最小 `64KiB` |
| `--restart` | 忽略本地进度、重新解析源版本；仍复用远端相同内容 |
| `--dry-run` | 只读预检，输出逐文件计划及 JSON 汇总 |
| `--chunk-size SIZE` | `1MiB`，控制读取缓冲 |
| `--attempts N` | `3`，每项操作最大尝试次数 |
| `--progress auto\|on\|off` | `auto`，终端显示两层进度条，stderr 重定向时自动关闭 |
| `--timeout SECONDS` | `120`，socket 读取超时 |
| `--hf-endpoint URL` | `HF_ENDPOINT` 或 `https://huggingface.co` |
| `--ms-endpoint URL` | `MODELSCOPE_ENDPOINT` 或 `https://modelscope.cn` |
| `--version` | 输出包版本 |

大小参数支持整数 bytes、KiB/MiB/GiB、KB/MB/GB。模式针对仓库相对路径使用 `fnmatchcase`，区分大小写；不是正则。请给模式加引号防止本地 shell 展开。源和目标位置参数建议写在多值筛选参数之前。

### `--repo-type auto` 的含义

`auto` 是仓库类型自动探测，不是根据文件扩展名推测。工具会分别查询 HF 的模型仓库和数据集仓库：

- 只有一种类型可访问：自动使用该类型，目标仓库沿用相同类型。
- 同一个 `owner/repository` 同时存在模型和数据集：报歧义，需传入 `--repo-type model` 或 `--repo-type dataset`。
- 两边都查不到：报仓库不存在或当前 token 无法访问。
- HF 对不存在或不可见的类型也可能返回 401/403。自动探测会查询两种类型，并选择唯一成功的匹配；都未成功时报告鉴权/权限错误。网络和服务端错误仍会中止探测。

私有仓库可能通过 401/403/404 隐藏其存在，因此探测结果以当前 HF token 可见的仓库为准。显式指定类型时只查询该类型。

## 仓库、版本与更新

- 目标不存在时自动创建。可见性 `auto`：公开源对应公开目标；私有/gated 源或无法确认源可见性时创建私有目标。已有仓库不改变可见性。
- 创建时尽量沿用 HF `cardData.license`；无法取得单一许可证值时提交 ModelScope 的 `Other`，不会擅自声明 Apache/MIT 授权。原仓库许可证文件照常传输。服务端不接受的许可证值会明确报错。
- 新仓库从 `master` 开始。指定其他目标分支时，该仓库和分支需要事先存在。本版不自动创建分支或 tag。
- 比较目标当前文件的大小和 SHA-256。相同内容跳过，不同则默认更新并产生提交；无法确认相同也按冲突策略处理。不会因为仓库已存在或两端 commit ID 不同而报错。
- `--on-conflict error` 在上传前遍历选中文件检查冲突；无法确认相同也拒绝写入。没有源 SHA-256 时，真实执行可能先流式读取计算；dry-run 不下载内容。
- `--on-conflict skip` 保留同路径不同内容，仍上传不存在的文件。
- 不删除仅目标存在的文件。目录与文件的路径冲突不会隐式删除目录。
- 每个文件单独提交，没有整个仓库的原子事务。预检不能锁住远端分支，建议同一目标分支不要并发写入。

## 恢复与缓存

中断后执行**同一条命令**即可恢复，不需要 `--resume`。缓存以任务身份哈希命名，存储 SQLite 元数据，不保存 token、签名 URL 或文件内容。不同仓库、分支、筛选条件和冲突策略隔离状态，同一任务禁止多进程同时执行。

- 未完成任务固定 HF commit，即使源 `main` 前进也继续原快照。
- 任务完成后再次运行，会重新解析源 revision，从而同步新版本。
- 每次核对远端当前文件；目标被外部修改/删除时会按冲突策略重新处理，不仅信任本地“完成”标记。
- blob 已上传但提交失败：重新协商后复用服务端仍保留的完整 blob。
- 文件上传中断：该文件从头传，**不支持文件内部字节偏移续传**。
- `--restart` 清除当前任务本地进度并重新解析源版本；不意味着强迫服务器再次接收相同 blob。

缓存超限会按最久未使用顺序删除**已完成且未被占用的任务数据库**。正在运行和未完成的任务不会被自动清除。若无法释放足够空间则保留检查点并报错，需增大额度或手动删除不再需要的任务状态。额度是检查点操作后的预算检查，SQLite 当前事务及 journal 可能暂时超过预算；不是硬磁盘配额。缓存清理不会删除远端内容。

`--dry-run` 不创建缓存目录、不清理缓存、不修改检查点，不创建远端仓库、不调用上传协商/PUT/commit。已有未完成检查点时预览其固定版本；`--restart --dry-run` 预览当前源版本。无法仅从元数据确认相同时标记 `unknown`。JSON 的 `bytes` 是选中文件总大小，不是必然产生的网络流量；`would_commit`/`would_skip`/`would_error` 表示按当前策略计划执行的文件数，预览成功不等于真实上传权限已验证。

## 空间与协议限制

大文件通过默认 1 MiB 有界缓冲从 HF HTTP 响应送入 ModelScope PUT。此缓冲不是服务端 multipart 分片。HF 提供 LFS SHA-256 时无需先读取计算；无哈希的大文件先流式哈希、再流式上传，两遍读取但均不落盘。

非 LFS 小文件（≤1 MiB）在内存内编码提交；README.md、.gitattributes、configuration.json 保持普通文件，超过上限时失败。内存还包括 HTTP/TLS 缓冲、目录页和小文件 Base64/JSON，因此缓冲大小不等于进程 RSS。目标目录元数据最多缓存一个目录的 3000 项；无法完整列举的 MS 模型目录会明确失败，不把截断结果误认作文件不存在。

网络错误和 429/部分 5xx 有有限次重试。大文件大小、SHA-256 在流式传输时校验。串行、每文件一次 commit 的方式可能受碎文件上传限流影响。HF 超大文件 HTTP resolve 可能受限，本版不实现 Xet 重建，也不回退到完整落盘下载。

## Python API

```python
import os
from hf2ms import Transfer, TransferOptions

result = Transfer(
    TransferOptions(
        source="org/model",
        destination="user/model",
        repo_type="auto",
        hf_revision="main",
        ms_revision="master",
        on_conflict="overwrite",
    ),
    ms_token=os.environ["MODELSCOPE_API_TOKEN"],
    hf_token=os.getenv("HF_TOKEN"),
).run()
print(result)
```

0.2.2 支持元数据 API 的同源重定向、明确列出的官方域名别名跳转，以及 `hf-mirror.com` 到 HF 官方站的单向跳转。POST 仅在 307/308 时保持方法和请求体重发；已消费的上传流不会自动重放。HTTP 错误显示方法和域名，不输出签名查询参数。HF 一种类型返回 401/403 时仍探测另一种类型。

0.2.1 将 CLI 参数 `--visibility` 改为 `--ms-visibility`；Python API 的 `visibility` 字段和已有缓存身份保持不变。

0.2.0 将旧参数 `--revision`、`--destination-revision`、`--state`、`--recheck` 分别改为 `--hf-revision`、`--ms-revision`、`--cache-dir`、`--restart`；Python options 同步更名。旧 `.hf2ms/state.sqlite3` 不自动迁移，远端完整 blob 仍可复用。

## 开发与验证

```bash
uv sync --locked
uv run pytest
uv run ruff check .
uv build
```

测试使用本地 HTTP 服务验证实际流式 PUT、断线恢复、自动创建、冲突策略、预览、缓存保护和 token 参数。CI 配置覆盖 Linux/macOS/Windows 与 Python 3.9–3.14。已通过真实带凭据的 dataset 操作验证已有 LFS blob 复用、文件 commit 成功及目标大小/SHA-256 一致。完整仓库及真实流式 PUT 验证仍待完成。HF 镜像可能将元数据请求重定向到 HF 官方域名，因此运行网络仍需要能连接该域名。

协议参考：[ModelScope REST/LFS](https://github.com/modelscope/modelscope_hub/blob/main/src/modelscope_hub/_legacy_api.py)、[HF Hub API](https://huggingface.co/docs/hub/api)。

0.2.3 对 LFS batch 返回的对象缺失、重复或对象列表格式异常也执行有限重试。`--attempts` 默认为 3，包含首次尝试。耗尽后以非零退出码结束并保留进度。直接重跑原命令（无需 `--restart`），已提交且内容相同的文件会跳过，未完成的文件从第 0 字节重传。对象列表字段缺失或格式异常仍会报错；显式空列表的处理见下面的 0.2.5 修正。不会自动跳过失败文件继续其他文件。

0.2.4 修复 dataset 分支列表接口返回 404 导致无法续传的问题：仅对默认 `master`，改用文件列表确认版本可访问，写入权限仍由 commit 接口验证。其他分支仍需分支列表接口正常工作；不会忽略 401/403 或文件列表错误。ModelScope API 报错现在显示原始请求路径，不输出查询参数和签名跳转地址。

0.2.5 修正旧版 ModelScope LFS 的复用语义：显式 `objects: []` 表示无需上传 blob，与官方 SDK 行为一致。随后仍需提交文件引用，只有 commit 成功才记录完成。对象列表字段缺失、格式错误、非空列表中 OID 重复或不匹配仍会报错。

0.2.6 增加两层进度条：`Files` 统计成功提交和跳过的文件数；当前文件显示字节数、速度和预计剩余时间，处理结束后自动消失。哈希读取单独显示，开始上传前归零。`HF -> MS` 统计上传器已读取的字节（不是服务端确认收到的字节），之后显示 `Committing` 直到提交成功。小文件内联上传显示 `HF read` 和 `Committing`；复用已有 blob 不虚增传输字节。重试时当前文件进度归零，失败文件不增加总进度。警告和错误仍保留，但不留下已完成文件的进度条。

可用 `--progress on` 强制显示，或 `--progress off` 使用普通逐文件日志。进度输出到 stderr，dry-run 的 stdout JSON 不变；切换该参数不影响续传。为获得筛选后的精确文件总数而不把全部元数据存入内存，启用进度条时会额外流式遍历一次文件元数据，不落盘缓存文件内容。

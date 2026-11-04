# CDS 0.6.10 Linux 资格与交付索引

**Linux 正常安装版的本地资格已闭环，包括真实 96 图/Jinja 长任务。** 最终 source commit/tag、公开资产下载及发布后安装由外部交付回执确认；此处不预造未来结果。Linux 独立发布，其他平台不自动继承资格。

## 固定组件与候选身份

- Codex CLI `0.164.0-alpha.7`，upstream `fe136d4e70ef2b5bbed40a2d22b3aabf267796d3`；固定 DeepSeek `deepseek/deepseek-v4.1-flash-1017`。
- Linux P35 raw recipe `ecb4674b926992022d36cc0eef527627a415b61633356129b44fd9add864c83e`；target recipe `08efc55024717f591c1b032e14c0401bad718bbb0d3828087459085536d6e95b`。
- 实际正常 CLI SHA `3682953b5ae307593bdfe0e6f8e41fdd269dca55af3dcb90797eb87b631cb626`；原归档 SHA `be4192cfb7632ed99f6106d7b429d20097bda58c2648373ec96d0bbe1bc5b6e1`，159,347,453 字节。
- 原生 9,365 文件实际 inventory scope `52b3a0973e8b91dbd38db34610dc162b8bc7664b3df19c8c1292fff084fc9ffc`，实际清单文件 SHA `7d8facf2177e5b0e4d471bd5a17aac8d7683d520bfa25abf8f09e68861de8172`。
- 新 0.6.10 正常安装复用同一 native 目录/文件；仅 Python 与 distribution manifest 更新。最终 tag/source ID 由实际外部发布回执记录，不用文档内未来自身 SHA 冒称发布提交。

## 已完成证据

| cohort | 结果/范围 | 原 result SHA256 |
| --- | --- | --- |
| 新 3 原生 reset/oldgap/hook 场景 | 3 通过，与原 64 分批 | `bd56e4b1c464c342f906c7c570b81602573e5fc3c6bd53ab5857a99dec8b9cbb` |
| 原 64 容量机制 | 64 通过；同 sealed binary 前后相同；无强制清理、未知存活或残留 | `75f8f0a7747f182bd5115e11e4fc459f6d7fc21317d0bea610a4e4f0b91d9be6` |
| 正常 installed native14 | 14/0失败/0跳过，208.87 秒，900 秒原预算；真实安装来源 | `27931d41b6d7b12b708951cddf715676c26b081e7ca27dbb7dd07805147e9536` |
| 新 CLI public8 | 8 场景 errors[]/noinstall；2 receive window+6 early cancel | `38915980772183c89ceb74e53f45e17a482cdea2a832da4f904b58f80288af23` |
| strict2cold | old P27 实际 orphan/CP6；固定6预算覆盖14图、fresh7、pending原样、fence/epoch/once/自然EOF | `0467519d0ad0a85d2866f0f64d7bc692ad8837b7f4aadf53595d1ee31c37d8a6` |
| 0.6.10 Linux installed Python | 297/0失败/0跳过；32模块前后SHA/来源一致，原HTTP helper | `13ad3aa2ecfb4b294edf41fea5d52edcd99ddf7e7c660809164d74e436b7bca3` |
| 实际长期历史技术证据 | 318,538,647字节旧前缀保持；compacted计11项后12普通DeepSeek完成 | `51ef3eb271ce3caa1f9141eb938e184c05125db6ed02694bdcedc05c8108e3bd`、`cc454dae2c2e60ac7ba3a20ce3e193685655a6c26acc019d00bab3ecc7b2c586` |

原生 unit 与生产 CLI 是不同产物；不能把 coreopt0 测试程序称为生产 opt3。不同 cohort 不相加为全部接口覆盖率。长期历史的字节数不代表网络请求大小；技术 JSON 不含企业正文，公开索引也不发布 thread/turn/user 路径等标识。

## 失败与观察边界

之前 native14 的7失败/7通过、P34的3失败/11通过、原64的1失败/63通过均保留；合法 UTF-8 分片使旧 mock 的不可恢复前提失效后，仅强化了已批准夹具拒绝谓词，原断言/预算未降。source hook 夹具和控制器路径/语法/换行 guard 失败分别留原证，不改成产品通过。

native14 有一条已消失的短命 child edge_changed 观察记录；final unknown/errors/remaining为空，不等于从未存在观察不确定性。原 provider/HTTP/流失败和中途失败保留，不因最终成功消失。

## 真实96图/Jinja最终资格

最终私有资格索引 SHA `b4612cbacca01eb15ce65347da8b7a11500adac22cadb937421da3be8945ca7f`，核心结果 SHA `24945235966b3a31adff482ba82319c158164eb37c75d00e4c331fe586ba92eb`。只公开下面的技术摘要及哈希，不公开原始 QA、模型正文、图片内容、工具参数、generation ID 或用户路径。

- 全96原图记录及summary严格匹配，原PNG字节不变。独立工作区912、heldout908，均零失败/错误/跳过。
- 6次自然压缩；66个唯一大图片请求的完成链关联成立（62 ordinary、4 compaction），关联证据 SHA `d10bf3b930320bbcfe69386c9651bcdc9aacfb2ac06631eff8ce13e196e56e8a`。不是把同请求的双账本记录累计成132，也不独立声称原始socket体测量。
- cold4、cold8均通过；原生既有readonly child收到新最终review turn并完成，不是新建子线程，也不是只用provider探针。
- 原EOF失败事务ledger一次并保留原失败；独立新事务真实RPC interrupt及worker退出通过，第二ledger也一次。本地native/tool/provider最后均退役，无强杀未知所有者。
- 173次实际native工具调用的审计未发现gold/controller/credential目标读取。原32安装模块与163源输入首尾一致；仍使用CLI `368295…` 与安装 `c39015…`。
- 5个选定真实generation的后续GET记录核对固定模型/Morph/配置快照。真实长图片组合是普通Morph+peerMorph，普通默认官方DeepSeek保持。

经过原失败与多次受控继续完成；不是一次连续75分钟运行，原phase timeout不变。R443同轮5图witness缺口、旧namespace PID夹具误判、EOF与RPC取消的区别、429、400、502、generation即时404后延迟200以及全部provider CAS切换均保留。Prime文本peer兼容不外推到22图续接（真实400）。

## 图片缓存与云端不确定性

已知完成请求pin=0，未知上传资产=0；3个消费结果未知请求仍保护36个pin（原EOF、新RPC计划取消、无generation ID的502）。一次有界正常reconcile后保留未知，没有强清。不能把本地进程结束或任务成功写成所有云对象已删除。证据摘要 SHA `526e6bb0ab5be6731fa164fea840aa276d187f9d1d56a6568a21025e7ec2ecd3`。

## 公开交付

Linux本地资格已闭环。最终发布仍使用现有package/install流程：实际final source/tag、Linux规范资产名与URL、公开下载SHA和normal no-activate安装读回必须来自真实回执；本地JSON中的URL不代表下载已验证。tag内未知future字段保留null，公开交付回执在发行页/后续索引引用，不移动旧tag或生成自引用hash。

Windows P36正常构建及old-home Session3路径、macOS新资格、CADSG继续各自验收；新Linux tag不携带冒称ready的旧平台manifest。旧0.6.9 tag/入口不变。

OpenAI云权益、模型质量/固有模态、金融体育私有数据与CADSG/App另列。平台、合法skip、历史计数继续绑定原版本；不改旧ci/reuse/retained硬pin来制造新资格。[机器可读索引](qualification-index.json)给出当前明确状态。

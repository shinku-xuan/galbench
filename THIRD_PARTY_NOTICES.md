# galbench 第三方资源与许可声明

核对日期：2026-10-29。适用资源：当前 `dev-v2.0` 题库、`spec/v2.0` 流程样题及网页素材。

## 许可范围

galbench 自行编写且有权许可的程序源码、页面样式与交互、题目格式和评分规则采用根目录 [LICENSE](LICENSE) 中的 **MIT License**，版权署名为 `Copyright (c) 2026 二阶堂真惨`。

**根目录 MIT 不覆盖仓库中的全部素材。** 游戏图片、封面、社区立绘、游戏台词、来源数据库及相关商标各自保留其权利与许可。本声明不会代替权利人的授权，也不表示相关作者、游戏公司或模型厂商认可本项目。题库的整体可复用范围须结合下列条款判断，不能标为“所有内容均为 MIT”。

逐文件来源、SHA-256、题目编号、剧情脚本版本及许可状态见 [THIRD_PARTY_ASSETS.json](THIRD_PARTY_ASSETS.json)。清单中的 `source_recorded_permission_pending` 表示已记录来源、尚未取得可核实的再分发授权；`references_permission_pending` 表示生成素材的参考形象权利仍待确认；这些状态都不是许可名称。`source_checked` 仅表示题目核对状态。

| 资源 | 当前数量 | 许可或状态 |
| --- | ---: | --- |
| LobeHub 品牌 SVG | 11 | MIT；保留 LobeHub 原署名；商标权另行保留 |
| The Question 既有中文剧情片段 | 6；流程样题另复用 1 段 | 上游 MIT；保留作者及翻译贡献者署名 |
| 《片轮少女》既有中文剧情片段 | 94；流程样题另复用 1 段 | CC BY-NC-ND 3.0；有非商用、禁止演绎限制 |
| 角色识图图片 | 400 张；流程样题另复用 3 张 | 从 VNDB 记录取得；图片再分发授权待确认 |
| Steam 游戏封面 | 38 | 封面权利归各自权利人；再分发授权待确认 |
| VNDB 游戏封面 | 107 | 图片不属于数据库许可的统一授权范围；再分发授权待确认 |
| The Question 标题画面预览 | 1 | 来自官方 Ren'Py 示例；上游 MIT，保留原贡献者权利 |
| 社区 AI 拟人立绘及预览 | 11 张原图、11 张缩略预览 | 原作者、公开出处与再分发授权待补充 |
| 参考上述形象生成的 CG | 3 | 已记录生成来源；参考形象权利待确认 |
| VNDB 衍生结构化数据 | 分布于文本题、识图题及流程样题 | ODbL 1.0 / DbCL 1.0；图片、介绍文字不因此获得授权 |

## galgame 素材非商业使用声明

**本项目声明：galbench 使用的 galgame 角色图片、游戏封面、游戏剧情台词及其选段，仅用于非商业的模型能力评测、研究交流和界面展示，不将这些素材用于销售、付费分发或商业推广。**

这是本项目对自身使用用途的承诺，不是素材权利人授予的新许可，不修改原始许可、不限制原许可已经允许的使用，也不为授权待确认的素材补充授权。使用者复用相关素材时，仍须遵守各项原始许可及实际权利人的要求。项目原创代码继续按 MIT License 授权。

## LobeHub Icons

位置：`galbench/static/ai-cast/icons/*.svg`。来源：[LobeHub Icons](https://github.com/lobehub/lobe-icons)。版权署名：`Copyright (c) 2023 LobeHub`。

保留上游 MIT 全文：[原位置](galbench/static/ai-cast/icons/LICENSE)、[集中副本](licenses/LobeHub-Icons-MIT.txt)。逐个图标的原始下载地址见资源清单。DeepSeek 图标在本项目中使用 `#4D6BFE` 蓝色配色。

MIT 许可针对图标文件的著作权，不构成品牌名称、标志或商标的许可。图标仅用于识别品牌及界面展示，不暗示合作或官方背书。

## 剧情文本：The Question

作品作者：**mikey**（按上游游戏署名）；既有简体中文译文：Ren'Py 上游贡献者。

- 来源：[Ren'Py 官方仓库中文脚本](https://github.com/renpy/renpy/blob/80b3e8236e3d022bdcc103b4833758c9b4ea6666/the_question/game/tl/schinese/script.rpy)。
- 固定版本：`80b3e8236e3d022bdcc103b4833758c9b4ea6666`。
- 许可依据：[同版本上游许可说明](https://github.com/renpy/renpy/blob/80b3e8236e3d022bdcc103b4833758c9b4ea6666/sphinx/source/license.rst)。归属与 MIT 许可全文：[RenPy-TheQuestion-NOTICE.txt](licenses/RenPy-TheQuestion-NOTICE.txt)。
- 使用内容：既有译文片段；清理游戏显示格式、解析说话人，部分题目合并同一分支的相邻片段。本项目另外编写参考总结和评分标注。

题库目录和流程样题目录内也保留 `UPSTREAM_RENPY_NOTICE.txt`。本项目未打包 Ren'Py 引擎、可执行游戏或其二进制依赖；引用该作品不意味着将 Ren'Py 的全部依赖引入本项目。

书架中的 *The Question* 预览使用同版本 `the_question/game/gui/main_menu.png` 官方标题菜单背景，并在界面叠加作品名称；未使用其他同名游戏的封面。图片缩小至 768×432、从 PNG 转为 JPEG，画面未重绘。上游许可说明将示例美术按相同 MIT 条款提供，原美术贡献者的权利与许可继续保留，详见集中许可副本。

## 剧情文本：《片轮少女》/ Katawa Shoujo

原作：**Four Leaf Studios**，2012；[原作网站](https://www.katawa-shoujo.com/)。既有中文翻译按原游戏署名归 **Katawa Girl Translation Team** 等原贡献者。

- 收集来源：社区项目 **Katawa Shoujo: Re-Engineered / Fleeting Heart Studios**，并非原作官方授权本项目的证明。
- 固定版本：`cb98e1170c918efe834b24473e3e4aca6c9d05e3`。
- 来源脚本：该版本 `game/tl/zh_hans/script-a1-*.rpy` 及五条角色线路的 `script-a2-*.rpy`；清单保留每题的文件、行号或翻译块标识。
- 许可依据：[该版本上游 README](https://github.com/fleetingheart/ksre/blob/cb98e1170c918efe834b24473e3e4aca6c9d05e3/README.md) 区分 MPL-2.0 程序代码与原作素材的 **CC BY-NC-ND 3.0**。本项目使用的是既有剧情译文，未引入该项目引擎代码。
- 完整署名与原许可全文：[KatawaShoujo-NOTICE.txt](licenses/KatawaShoujo-NOTICE.txt)；[CC 官方许可页面](https://creativecommons.org/licenses/by-nc-nd/3.0/)。题库和流程样题目录另保留 `UPSTREAM_KATAWA_SHOUJO_NOTICE.txt`。

这些片段不能重新授予 MIT、商业使用或自由改编权限。原许可要求保留署名、限制商业使用，并限制演绎作品的传播；复用时应以许可全文为准。当前预处理只做原译文抽取与显示格式转换，另附独立的评测标注；本声明不对“选段及其具体呈现是否满足原许可全部条件”作额外授权或保证。需要商业分发、改写或超出原许可的使用时，应另行取得相关权利人的许可。

## VNDB 数据与角色识图图片

来源：[The Visual Novel Database](https://vndb.org/)、[官方 API 说明](https://api.vndb.org/kana)、[Data license](https://vndb.org/d17)。文本与识图题涉及作品、角色标识、姓名、别名、关联作品及其他结构化资料。对其中来自 VNDB 的数据库部分，保留 **Open Database License 1.0（ODbL）** 与 **Database Contents License 1.0（DbCL）**；本项目有权许可且构成该衍生数据库的整理贡献，也按 ODbL 1.0 提供。

许可全文：[ODbL-1.0.txt](licenses/ODbL-1.0.txt)、[DbCL-1.0.txt](licenses/DbCL-1.0.txt)。复用时应保留来源与许可；对衍生数据库的公开使用、再分发以及同许可共享义务，应遵守 ODbL 全文。数据库许可与其中单项内容的权利是不同层次，不能据此把游戏素材一并转为开放许可。

**VNDB 的图片、视觉小说介绍、角色介绍不在上述数据许可的统一授权范围内。** 当前 400 张角色图片的 VNDB 页面、图片地址、角色及作品关联、文件哈希均已记录，但尚未逐张取得实际图片权利人的再分发授权；作品开发商字段只用于来源识别，不表示已确认该公司就是图片著作权人。流程样题的 3 张图片是这 400 张中的副本，不是新增素材。

文本题主要以资料中的身份、关系、剧情或曲目事实为依据，由编者编写题面；部分题目转述人物介绍。数据库许可不能替代对这类介绍文字表达的权利核查，题面也不能因转述就一概视为获得授权。资源清单将其标为 `source_expression_review_pending`，保留每题事实依据和来源地址。

少数文本题还参考游戏官方网站、维基百科及攻略站等资料。它们不属于 VNDB 的许可范围，原网页全文、网页快照与游戏脚本没有随这些文本题打包。清单列出每题来源用于核查，不声称已取得这些网站正文的再分发授权。

## Steam 游戏封面

位置：`galbench/static/covers/steam-*.jpg`，38 张。作品名、商店链接和图片 CDN 地址见 [catalog.json](galbench/static/covers/catalog.json) 及资源清单。

封面来源于 Steam 商店及其图片 CDN，图片著作权归各自实际权利人。商店公开展示、可下载或保留来源链接，都不等于本项目取得再分发许可。本项目尚未取得这些封面的逐项开放授权，不以 MIT、CC 或 Valve 名义对其重新授权。

## VNDB 游戏封面

位置：`galbench/static/covers/vndb-*.jpg`，107 张。原始图片来自相应作品的 VNDB 公开记录，作品关联、图片地址及来源评分见封面目录与资源清单。图片经过人工查看，展示副本仅做等比例缩小与 JPEG 压缩优化，未裁剪或重绘；清单分别记录原下载文件与展示副本的哈希。部分来源评分非零的封面保留其原始数值，不将人工查看伪称为上游零分评级。

这些游戏图片不在 VNDB 数据库许可的统一授权范围内，本项目尚未取得逐张再分发授权。它们依本项目的素材非商业使用声明用于评测界面展示，版权保留，不重新授予 MIT 或其他开放许可。

## 社区 AI 拟人立绘与生成 CG

位置：`galbench/static/ai-cast/`。社区原图共 11 张：`deepseek.png`、`glm.png`、`kimi.png`、`muse.png`、`gpt.jpg`、`claude.jpg`、`gemini.jpg`、`grok.jpg`、`minimax.jpg`、`qwen.jpg`、`seed.jpg`。

这些原图由项目维护者从社区收集后提供，本项目没有已核实的原作者署名、公开原帖地址或再分发许可。提供文件本身不构成原作者的授权；本声明不会猜测作者、把模型厂商当作插画作者，也不把这些文件纳入项目 MIT 许可。

标题页另使用 `card-*.jpg` 共 11 张轻量预览，来源逐一对应以上原图，仅做等比例缩小和 JPEG 格式转换，未裁剪或重绘；上半身取景由页面 CSS 完成。完整原图继续保留，预览继承相同的来源与待确认授权状态。

以下 3 张图片使用内置 `imagegen` 工具生成，参考关系如下：

| 文件 | 参考形象 |
| --- | --- |
| `club-cg-v1.png` | DeepSeek、GPT、Claude |
| `arena-duel-cg-v1.png` | Gemini、GPT、Grok |
| `archive-table-cg-v1.png` | Claude、Qwen、Kimi |

生成来源、参考文件及成品哈希见清单。生成图片不等于消除参考角色设计、商标或其他第三方权利；这些 CG 暂不作 MIT 或其他无条件再分发授权。公开说明不包含本地路径、完整生成提示词或个人信息。

## 运行环境与外部 Harness

galbench 运行代码使用 Python 标准库及浏览器原生功能；当前分发文件不包含 Python 解释器、第三方前端依赖包、模型权重、外部 Harness 的 CLI 或 SDK。Codex、MiniMax Code、ZCode、Claude Code、Kimi Code、DeepSeek Harness、OpenCode、Qoder、WorkBuddy 由使用者另行安装和授权，适用各自许可与服务条款；本项目的适配代码不会改变它们的授权范围。

## 公开分发前的未决事项

代码的 MIT 许可已经给出，但当前完整素材包不能据此视为全部获得开放再分发授权。角色图、封面和社区立绘应补足授权、换用可再分发素材，或从拟公开版本中排除；生成 CG 仍需处理参考形象权利。《片轮少女》片段需保留原有限制，并核实选段的具体使用方式。未取得证据的项目在清单中继续标为待确认，不默认以“非商用”或“仅供学习”代替许可。

权利人如需补充署名、更新授权或要求处理素材，请联系项目维护者，并提供涉及的文件路径及来源证明。

========================================================
易班微社区自动发帖工具  v1.1.7
========================================================
适用系统：Windows 10 / 11（64 位）
更新日期：2026-11-02

一个 Windows 桌面工具：输入本人易班账号密码后，自动登录、自动生成帖子内容、
自动填写到 https://s.yiban.cn/bbs/post/create 并提交，最后给出成功 / 失败结论。

交付物：
  YibanAutoPoster.exe   主程序（单文件，双击运行，无需安装 Python）
  settings.json         配置模板（与 exe 放在同一目录即可生效）
  README.txt            本说明


------------------------
0. 先读这一段
------------------------
1. 本工具只用于操作你本人的易班账号，禁止用于批量注册、批量操控他人账号、
   刷屏、广告或任何违反平台规则与法律法规的用途。
2. 自动处理验证码是“尽力而为”：滑块轨迹模拟只对常见滑块实现有效，图片验证码
   OCR 需要额外组件；短信验证、人脸识别、身份验证无法自动完成，会转人工。
   平台改版或风控升级后自动处理可能失效，此时请在“验证码”设置里改回人工接管。
3. 帖子内容由模板、你预置的文案或第三方大模型生成。发布前请在预览区检查，
   内容是否合规由使用者负责。
4. 易班前端会更新。若工具提示找不到输入框 / 按钮，请保留日志、截图和页面 HTML。
5. 默认只发 1 篇，两篇之间至少间隔 10 秒（默认 30 秒），单次最多 100 篇。
   高频发帖可能触发平台限制（服务端返回 -1002），工具会识别并停止。
6. 请把程序放在普通可写目录（桌面、文档、D:\YibanAutoPoster 等），不要放在
   受管控的目录；受限目录会影响浏览器子进程与截图写入。首次运行若出现
   SmartScreen 提示，确认文件来源后选择“更多信息 → 仍要运行”。


------------------------
1. 运行环境
------------------------
· Windows 10 / 11（64 位），能正常访问 https://www.yiban.cn 与 https://s.yiban.cn
· 系统自带 Microsoft Edge（Win10/11 默认就有）。工具默认用 CDP 方式直接驱动
  本机 Edge/Chrome，不需要下载 WebDriver。
· 若要用 Selenium 引擎，需要 drivers\msedgedriver.exe（已随程序打包）。
· 首次启动会在 %LOCALAPPDATA%\YibanAutoPoster 下建目录存放日志与截图。


------------------------
2. 三步跑起来
------------------------
1) 双击 YibanAutoPoster.exe。
2) 在“01 ACCOUNT 账号”填写易班账号（手机号 / 用户名）和密码。
3) 在“02 CONTENT 内容生成”选择生成模式，点“生成预览”检查标题和正文（可直接改），
   再点“开始发帖”。

默认流程：启动浏览器 → 打开发帖页 → 未登录则自动登录 → 填写标题/摘要/正文 →
选择微社区与版块 → 点击发布 → 检测结果 → 输出结论。

成功时底部结果条显示白底墨字结论，并在“05 LOG 运行日志”里给出发帖成功的证据（页面提示、
接口返回、HTTP 复核结果）。失败时结果条改为黄底墨字并写明原因，同时保存现场截图、页面 HTML 和
接口记录。


------------------------
3. 界面说明
------------------------
1. 账号
   - 账号 / 密码：密码默认掩码显示，可勾选“显示密码”。
   - 记住账号密码：勾选后用 Windows DPAPI 加密保存到本机，只有当前 Windows
     用户能解密；取消勾选并运行一次即清除本机保存的凭据。

2. 内容生成
   - 生成模式：模板化生成 / LLM API / 手动预置内容。
   - 模板分类：日常寒暄、生活随感、古诗词、随机字母数字。
   - 话题 / 关键词：喂给模板或 LLM 的主题。
   - LLM 区：OpenAI 兼容接口的 Base URL、API Key、模型名、温度。
   - 预置内容…：打开编辑器，逐条维护“标题 + 正文”，可选按顺序或随机取用。

3. 发帖设置
   - 微社区 / 版块：填页面上的名称；留空表示用页面默认的第一项。
     只填名称（例如“XX学院微社区”），不要粘贴网址或 %2F 这类编码路径。
   - 间隔（秒）：10-86400，默认 30。
   - 连续篇数：1-999。第一篇用预览区文字，后续篇自动重新生成。
   - 失败重试：单篇失败后自动重试的次数（0-10）与间隔（5-600 秒）。
   - 重试时换内容：勾选后每次重试重新生成一版内容，避免“相同标题”。
   - HTTP 复核结果：发帖成功后再用浏览器 Cookie 请求“我的帖子”页做旁路验证。
   - 浏览器引擎：自动（Selenium 优先，失败转 CDP）/ Selenium / CDP（免驱动）。
   - 无界面：不显示浏览器窗口；遇到人工验证会直接失败，日常建议不勾选。

4. 帖子预览
   标题 / 摘要 / 正文都可以直接修改，发布时使用这里的文字。
   标题 5-30 字，正文 1-10000 字，右侧实时显示字数。

5. 运行日志
   实时输出，同时写入本地日志文件。失败时的截图、HTML、接口记录在截图目录。

底部按钮：生成预览、开始发帖、停止、保存设置、验证码、打开截图目录、清空日志。


------------------------
4. 三种内容生成模式
------------------------
【模板化生成】
内置日常寒暄、生活随感、古诗词（8 首公版古诗）、随机字母数字四类模板。
每次生成会随机组合段落与标题，标题自动裁剪到 30 字以内，避免短时间内重复。

【LLM API】
调用任意 OpenAI 兼容接口（OpenAI、DeepSeek、通义、Moonshot、本地 Ollama 等）：
  Base URL 例：https://api.openai.com/v1
              https://api.deepseek.com/v1
              https://dashscope.aliyuncs.com/compatible-mode/v1
              http://127.0.0.1:11434/v1
工具会请求 {base_url}/chat/completions，要求模型只返回
{"title": "...", "body": "..."}，并对返回内容做清洗与长度校验。
Key 会明文保存在 settings.json，请注意本机安全。

【手动预置内容】
在“预置内容…”里维护一组标题与正文：
  · 按顺序：第 1 篇用第 1 条，第 2 篇用第 2 条，用完循环；
  · 随机：每次随机抽一条。
标题留空时自动截取正文首行；正文必填。

导入 / 导出（v1.2.0 起）：
  · 预置内容窗口左下角新增「导入…」「导出…」按钮；
  · 导入支持 .json 与 .txt，读完会问「追加」还是「替换」；
  · .txt 格式：每条之间空一行，第一行是标题，其余是正文；「【1】」「1.」「- 」这类序号会自动去掉；
  · 随包提供 presets\正能量预置内容.txt / .json（60 条，分家国情怀、青春奋斗、校园生活、
    志愿服务、传统文化、生态文明、科技创新、致敬劳动者八类）。点「导入…」默认打开该目录，
    选文件即可；「导出…」可备份或改完再导回；
  · txt 可用记事本 / Word 编辑（Word 里另存为「纯文本」），UTF-8 或 GBK 都能读。


------------------------
5. 登录与验证码
------------------------
登录流程：
1. 先打开发帖页；若被重定向到登录页，则打开 https://www.yiban.cn/login?go=...
2. 自动填写 #account-txt / #password-txt 并点击“登录”。
3. 出现验证时按下面的策略处理。
4. 登录成功后回到发帖页继续。

验证码策略（“验证码”按钮里配置，默认 captcha.mode=auto）：
  点击方式：验证码默认用系统真实鼠标点击（captcha.click_mode=system），
            窗口不在前台或找不到窗口时自动退回浏览器事件；滑块轨迹仍走浏览器事件。

  auto    先尝试自动处理：外接服务（若配置）→ 离线识图（颜色 + 形状）→
          视觉模型点选 → OCR 文字点选（需 ddddocr）→ 模拟滑块拖动 →
          按“自动尝试次数 / 间隔”重试；仍失败则转人工接管。
  manual  只人工接管：弹出黄色提示条，最小化工具窗口，你在浏览器里完成后
          工具自动继续（检测到验证框消失或页面跳转）。
  http    只调用你自己的 HTTP 识别服务（截图 → 服务返回点击坐标 → 自动点击）。
  command 只调用你自己的本地程序（stdin 传 JSON，stdout 返回 JSON）。

能力边界（重要）：
  · 图形点选（数美“空间逻辑验证”，按文字找图形）：先用离线识图（不需要账号），
    再用视觉模型兜底。
  · 离线识图：纯 Python 解码截屏 → 把有颜色的像素做连通域 → 用填充率、宽度轮廓、
    竖直棱的亮度突变判断 圆锥 / 圆柱 / 方体 / 球，再按题目里的“颜色+形状”点击；
    支持“最小的 / 最大的”这类限定词，识别不出形状（如六棱柱）时按颜色 + 大小兜底。
    不需要任何 Key，单张图约 0.1 秒。代价是规则写死：图形互相重叠、题目只写形状
    不写颜色、或出现规则外的图形时会失败，失败就换图重试或交给视觉模型。
  · 验证码图片没加载出来（提示“网络不佳，请刷新重试”）时，工具会自动点刷新并等待重载，
    不会把错误提示当成题目去识别。
  · 视觉模型：默认开启，复用“2. 内容生成”里的 LLM 配置；模型必须支持图片输入。
    识别失败按配置重试，最后回退人工。
  · 文字点选 / 图片验证码：需要 ddddocr，随包 exe 未内置。需要时：
        pip install ddddocr
        build.bat
    （ddddocr 依赖 onnxruntime，会让 exe 体积明显变大。）
  · 滑块：内置轨迹模拟是通用实现，数美风控升级后可能不通过；不通过就重试并回退人工。
  · 短信验证码、人脸识别、身份验证：无法自动完成，直接转人工。
  · headless（无界面）模式下遇到人工验证会直接报错停止。

数美这题长什么样、怎么解（实测样例见源码 assets\captcha-sample.jpg，已打进 exe）：
  题面是若干 3D 几何体（红/绿/蓝/黄色，圆锥/圆柱/正方体），题目文字“请依次点击：X、Y”
  显示在验证码面板里，图形本身没有文字。所以纯 OCR 只能读出文字、无法把“红色正方体”
  对应到图里的某个方块 —— 必须“看图 + 理解”，这正是视觉模型擅长的部分。

  工具的处理顺序：
    1. 从面板读题目文字（读不到就把整块面板截图给模型，让它自己读）；
    2. 把图形原图（600x300 的 JPG，直接从 castatic.fengkongcloud.cn 下载，比截屏清晰）
       连同题目一起发给视觉模型；
    3. 要求模型只返回 JSON：{"targets":[...], "points":[[x,y],...]}，坐标 0-1 归一化；
    4. 按顺序换算成页面坐标并点击，再看验证框是否消失；没消失就换图重试。

  配置（“验证码 → 图形点选”里，或 settings.json 的 captcha.vision）：
    base_url / api_key / model 三项留空 = 复用 content.llm 的配置。
    任何 OpenAI 兼容、支持 image_url 的模型都可以：
      gpt-4o / gpt-4o-mini        https://api.openai.com/v1
      qwen-vl-max                 https://dashscope.aliyuncs.com/compatible-mode/v1
      glm-4v-plus                 https://open.bigmodel.cn/api/paas/v4
      doubao-1.5-vision-pro       https://ark.cn-beijing.volces.com/api/v3
    点“测试识别”会用内置样例图跑一次，直接看到模型返回的坐标 —— 先确认模型能用，
    再让它去处理真实验证码。

  参考实现（GitHub 上同类思路，供替换或训练专用模型）：
    · sml2h3/ddddocr                  通用 OCR + 目标检测，可做文字点选
    · MgArcher/Text_select_captcha    文字点选（DenseNet + CTC）
    · Yang-hua6/yidun                 YOLOv8 + Playwright 点选方案，含 ddddocr 后端
    · tomysky/crack_20_captcha        20 种验证码识别思路汇总
  如果有自己的点选模型或打码平台，直接用“外接 HTTP / 外接程序”接进来即可，
  协议就是下面这套 points 坐标。

外接服务的接口格式：
  HTTP：POST JSON，字段含 image_base64 / image_width / image_height /
        viewport_width / viewport_height / attempt / instruction；
        返回 {"points": [[x, y], ...]}（截图像素坐标）。
  程序：从 stdin 读同样的 JSON，向 stdout 输出同样的 JSON。
  仓库里的 captcha_solver_example.py 是一个可运行的最小示例。


------------------------
6. 发帖与结果检测
------------------------
填写：
  · 标题框：input[placeholder*='标题'] / input[maxlength='30'] 等；
  · 正文：wangEditor（.w-e-textarea 内 contenteditable）。若页面改版把编辑器放进
    iframe，工具会自动进入 iframe 查找 contenteditable 并写入；
  · 摘要：有该字段时填写，没有则跳过；
  · 微社区 / 版块：MUI 下拉（.mdc-select → .mdc-list-item），按名称精确匹配，
    匹配不到再包含匹配；留空则保留页面默认值；
  · 草稿恢复弹窗：自动选择“不恢复”，避免覆盖已有草稿；
  · 封面：固定“无图”。

提交与判定：
  · 点击 button.btn-publish；按钮 disabled 时直接报错（通常是必填项未填）。
  · 成功证据：成功提示（“成功发布帖子 / 发布成功”）、跳转到我的帖子页、
    发帖接口返回 code=0/200 或 success=true。
  · 失败证据：服务端错误码与提示、页面错误文案、验证码未通过、
    频率限制（-1002）、相同标题弹窗。
  · 验证码拦截（接口 code=110）：按第 5 节策略处理，最多 3 轮。
  · HTTP 复核：成功后用浏览器 Cookie 请求 settings.json 里的 posting.verify_url
    （默认 https://s.yiban.cn/bbs/mypost）。找到标题会写进证据；找不到只提示，
    不影响成功结论（该页可能是前端渲染）。

失败重试：
  单篇失败后等待“重试间隔”秒再试，最多“失败重试”次；勾选“重试时换内容”会
  重新生成标题正文，降低“相同标题 / 内容重复”导致的失败。全部失败后停止本次任务。


------------------------
7. 配置文件 settings.json
------------------------
位置：优先 exe 同目录的 settings.json；该目录不可写时用
      %LOCALAPPDATA%\YibanAutoPoster\settings.json。
界面上“保存设置”会覆盖它。删掉文件后下次启动会按默认值重建。

{
  "schema_version": 1,
  "browser": {
    "engine": "auto",              // auto | cdp | selenium
    "browser_path": "",            // 留空自动查找 Edge / Chrome
    "profile_dir": "",             // 留空用数据目录下的 browser-profile
    "headless": false,
    "keep_browser_open": false,
    "window_width": 1280,
    "window_height": 900
  },
  "posting": {
    "interval_seconds": 30,        // 10-86400
    "repeat_count": 1,             // 1-999
    "retry_count": 2,              // 0-10
    "retry_interval_seconds": 20,  // 5-600
    "regenerate_on_retry": true,
    "verify_via_http": true,
    "verify_url": "https://s.yiban.cn/bbs/mypost",
    "community": "",               // 微社区名称，留空用默认
    "board": "",                   // 版块名称，留空用默认
    "cover_mode": 1                // 固定 1 = 无图
  },
  "content": {
    "mode": "template",            // template | llm | preset
    "template_category": "日常寒暄",
    "preset_items": [ {"title": "", "body": ""} ],
    "preset_pick": "sequential",   // sequential | random
    "llm": {
      "base_url": "https://api.openai.com/v1",
      "api_key": "",
      "model": "gpt-4o-mini",
      "temperature": 0.8,
      "timeout_seconds": 90
    }
  },
  "captcha": {
    "mode": "auto",                // auto | manual | http | command
    "attempts": 2,
    "retry_interval_seconds": 4,
    "auto_cv": true,               // 离线识图（颜色+形状）
    "click_mode": "system",        // system=真实鼠标 | browser=浏览器事件
    "auto_slider": true,
    "auto_ocr": true,
    "vision": {
      "enabled": true,
      "base_url": "",              // 留空复用 content.llm
      "api_key": "",
      "model": "",
      "timeout_seconds": 60
    },
    "http_url": "",
    "http_key": "",
    "command": "",
    "timeout_seconds": 60,
    "settle_seconds": 3,
    "fallback_manual": true
  },
  "security": {
    "remember_credentials": false
  },
  "advanced": {
    "browser_start_timeout": 45,
    "page_timeout": 30,
    "login_timeout": 300,
    "manual_timeout": 300,
    "publish_timeout": 90,
    "screenshot_on_error": true
  }
}

账号密码默认不写入 settings.json。勾选“记住账号密码”后，密码用 Windows DPAPI
加密后写入 %LOCALAPPDATA%\YibanAutoPoster\credentials.dat，只有同一 Windows
用户能解密；拷到别的电脑或别的用户下无法还原。


------------------------
8. 日志与截图
------------------------
日志：%LOCALAPPDATA%\YibanAutoPoster\logs\app-YYYYMMDD.log
截图：%LOCALAPPDATA%\YibanAutoPoster\screenshots\
  post-<时间>.png    失败现场截图
  post-<时间>.html   当时页面 HTML
  post-<时间>.json   当时捕获的接口记录
  captcha-*.png      验证码截图（外接服务或排错用）
崩溃：logs\crash-*.txt（未捕获异常）
日志不记录密码；账号只显示前 3 位加 ***。
排查问题时请提供：日志文件 + 对应时间的截图 + settings.json（去掉 Key）。


------------------------
9. 常见问题
------------------------
Q1 双击没反应 / 一闪而过
   A：用命令行运行 YibanAutoPoster.exe --selftest-basic 看输出；或到 logs\ 看
      最新日志与 crash-*.txt。部分安全软件会拦截未签名 exe，加入信任后重试。

Q2 浏览器没有启动
   A：确认本机有 Edge（C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe）。
      若用 Selenium 引擎，检查 drivers\msedgedriver.exe 是否存在且版本匹配。
      默认 engine=auto 会在 Selenium 失败后自动切到 CDP。

Q3 一直卡在登录
   A：看日志是否提示验证码 / 密码错误。验证码可把 captcha.mode 改回 manual
      人工完成；账号密码错误会直接报“登录失败：...”。

Q4 提示缺少 API Key
   A：LLM 模式必须填 Key。只想发当前预览内容时，切回模板或预置模式即可
      （预览里的文字会作为第一篇发布）。

Q5 找不到标题框 / 正文编辑器 / 发布按钮
   A：多半是页面改版。保留 logs 与 screenshots 目录反馈；临时可把 engine 改成
      selenium 再试一次（两种引擎的定位路径不同）。

Q6 发布按钮不可点击
   A：通常是必填项没填（标题、正文、版块）。检查标题 5-30 字、正文 1-10000 字，
      以及微社区 / 版块名称是否正确。

Q7 提示“相同标题”
   A：平台不允许短时间重复标题。勾选“重试时换内容”让工具自动换一版，或在
      预览区手动改标题。

Q8 提示频率限制（-1002）
   A：发得太快。增大发帖间隔、减少连续篇数，稍后再试。

Q9 配置文件读取失败
   A：settings.json 被改坏（缺引号、多余逗号）。删除该文件后重启，会按默认值重建。

Q10 记住密码后换电脑登录不了
   A：DPAPI 加密绑定 Windows 用户，换机器/换用户无法解密。这是预期行为；
      在新机器上重新输入账号密码即可。

Q11 换一台电脑后，验证码“点了没反应 / 识别不到”
   A：v1.1.9 起有三道保险，一般不需要手动处理：
      ① 窗口自动识别：不再只认启动时的进程号（Edge 兼容层会把浏览器重启到新进程），
         而是在所有浏览器窗口里按“页面标题 → 进程 → 前台 → 面积”打分挑，
         并跳过 Edge 的同步确认/欢迎弹窗；
      ② 点击后核对：页面里埋了 mousedown 计数器，系统鼠标点完 0.6 秒内计数没涨，
         就立刻改用浏览器事件点击，保证这一点确实落到页面上；
      ③ 手动锁定：验证码设置里点“捕捉浏览器窗口”，按提示在 5 秒内把鼠标放到
         浏览器网页区域，程序会记住这个窗口，之后只用它对坐标。
      仍不行时运行 YibanAutoPoster.exe --selftest-headful，看“系统鼠标点击”一项
      （会显示请求坐标与页面实际收到的坐标），把该行和前后几行日志发我。


------------------------
10. 从源码运行与打包
------------------------
环境：Python 3.10+（本机用 3.15 验证），Windows。

安装依赖：
  pip install -r requirements.txt
  pip install -r requirements-build.txt     # 打包用

源码运行：
  python main.py

自检（不登录、不发帖）：
  python main.py --selftest-basic     # 环境与模块
  python main.py --selftest           # 含浏览器：无界面打开登录页并截图
  python main.py --selftest-headful   # 含浏览器：带界面

打包：
  build.bat
  产物：dist\YibanAutoPoster.exe、dist\settings.json、dist\README.txt

手动打包等价命令：
  pyinstaller --noconfirm --clean --onefile --windowed --name YibanAutoPoster ^
    --icon assets\yiban.ico --add-data "assets;assets" ^
    --add-data "drivers\msedgedriver.exe;drivers" ^
    --collect-submodules selenium --hidden-import websocket ^
    --hidden-import websocket._core main.py


------------------------
11. 版本记录
------------------------
v1.3.0
  · 界面整体重做（终末地风格）：纸色底 + 白面板 + 1px 细线分区，不用圆角、投影和渐变；
    全屏唯一强调色只用在“当前 / 可点 / 要提醒”三处，失败与告警用黄底墨字，红色只留给删除。
  · 结构改成控制台顺序：顶部深色命令栏（// AUTO POSTER + 版本与产品块），下面三块并排
    01 ACCOUNT 账号 / 02 CONTENT 内容生成 / 03 POSTING 发帖设置，再往下 04 PREVIEW 预览、
    05 LOG 运行日志（深色打印区），底部结果条 + 一排切角按钮。
  · 按钮改成自绘切角块：悬停时强调色从左擦入，按下即触发；英文眉标用宽体大写、中文用黑体。
  · 标题/正文字数改成等宽数字的“实际 / 上限”，越界时用黄底标出；复选框换成自绘方框对勾。
  · 功能与配置文件完全不变，settings.json 无需改动。

v1.2.2
  · 修复“发帖时的图形验证码点了没反应”：验证码点击改为三层递进——
    ① 系统鼠标（会先把浏览器切到前台）② 浏览器原生输入事件（CDP 鼠标事件 /
    鼠标动作，属于可信事件，能穿透 iframe）③ DOM 事件链兜底；
    每层点完都核对“验证码是否换图 / 弹层是否消失”，没反应就换下一层。
  · 不再把“合成点击”当成成功：风控控件只认可信事件，旧版在 CDP 不可用时
    会退化成 element.click()，页面收不到，表现为图不换、框不消失。
  · 连续 2 次“点了没反应”立即停止自动尝试并转人工，不再空转 10 次；
    每次失败先刷新验证码换一张图再试，避免在同一张图上反复点。
  · 日志会写清每次点击用的是哪种方式（系统鼠标 / 浏览器输入 / DOM），
    排查时能直接看出卡在哪一层。

v1.2.1
  · 连续篇数上限由 100 提高到 999（间隔最低仍为 10 秒）。

v1.2.0
  · 预置内容支持「导入…」「导出…」：.json / .txt 两种格式，导入时可选追加或替换；
    序号自动清理，标题缺失时自动截取正文首行；txt 兼容 UTF-8 / GBK。
  · 随包附送「正能量预置内容」60 条（presets\ 目录）：家国情怀、青春奋斗、校园生活、
    志愿服务、传统文化、生态文明、科技创新、致敬劳动者，可直接导入后一键连发。
  · presets 目录同时打进 exe，exe 旁边没有该文件夹时也能从内置副本导入。

v1.1.9
  · “捕捉浏览器窗口”：验证码设置里新增按钮，最小化窗口后 5 秒内把鼠标放到
    浏览器网页区域，程序直接记下这个窗口句柄；运行时会优先用它做真实鼠标点击，
    也提供“取消锁定”回到自动识别。
  · 窗口识别更稳：不再只认启动时的进程号（Edge 兼容层会把浏览器重启到新进程），
    而是在所有浏览器窗口里按“页面标题 → 进程 → 前台 → 面积”打分挑选，
    并跳过 Edge 的同步确认/欢迎等小弹窗。
  · 点击后**核对页面是否真的收到**：页面里埋 mousedown 计数器，系统鼠标点完
    0.6 秒内计数没涨就立刻改用浏览器事件点击，避免“看着点了其实没点上”。
  · 修复打包版首次启动偶尔报 Target crashed：启动浏览器时清掉 PyInstaller 解包
    目录相关环境变量（_MEI*/PYTHONHOME/PATH），避免浏览器子进程加载到冲突 DLL。

v1.1.8
  · 修复“换一台电脑后系统鼠标点不到”：
    - 坐标基准改用网页内容子窗口 Chrome_RenderWidgetHostHWND 的客户区，
      不再用“窗口客户区 - 工具栏”估算，开侧边栏/改缩放/换分辨率都不偏；
    - 启动即声明 Per-Monitor V2 DPI 感知（SetProcessDpiAwarenessContext(-4)），
      多显示器不同缩放下坐标才是物理像素；
    - 自动关闭 Edge 的“同步确认/欢迎”等自带弹窗（会浮在页面上方吃掉点击），
      并补上 --no-first-run / --disable-sync / --disable-translate 启动参数；
    - 点击前用 WindowFromPoint 校验目标点是否真属于浏览器窗口，被遮挡就拒绝乱点、
      退回浏览器事件点击，并把遮挡窗口的标题写进日志；
    - 前台激活失败时用 AttachThreadInput 强制抢前台；点完校验光标确实落在目标点。
  · 自检新增“系统鼠标点击”一项（--selftest-headful 可见），
    会报告请求坐标与页面实际收到的坐标，换机器排错先用它。

v1.1.7
  · 新增“真实鼠标点击”：用 Windows SendInput 移动系统光标并按下（验证码专用），
    换算会考虑浏览器工具栏高度和 150% 缩放；窗口不在前台时自动退回浏览器事件。
  · 验证码对话框新增“真实鼠标点击”开关（默认开）。

v1.1.6
  · 点击坐标改用“截图那一刻”的元素矩形，避免弹层入场动画导致坐标漂移点空。
  · 截图前等待 0.25 秒让弹层稳定；日志输出每个图形的中心坐标与尺寸、
    实际点击坐标，并额外保存一张带红框标注的截图（*-marked.png），
    失败时一眼能看出点到了哪个图形。

v1.1.5
  · 修复：页面处于滚动状态时，验证码截图会截到空白区域（clip 用的是文档坐标，
    却传了视口坐标），表现为日志“找到 0 个图形”。改为整屏截图后按元素矩形裁剪，
    滚动与固定定位都不受影响。
  · 截图前等待验证码图片真正解码完成；图形太少时自动重截一次；
    日志会输出识别区域尺寸与截图路径，便于排查。

v1.1.4
  · 验证码对话框会直接显示“当前复用哪个地址/模型、Key 是否已配置”，
    没配 Key 时明确提示视觉模型会被跳过。

v1.1.3
  · 离线识图支持“最小的 / 最大的”限定词，支持六棱柱、四棱锥等说法；
    形状认不出时按“颜色 + 大小”兜底。
  · 验证码图片加载失败（网络不佳）时自动点刷新并等待重载，不再把提示文字当题目。

v1.1.2
  · 新增离线识图（纯 Python，无账号）：解码截屏后用颜色 + 轮廓识别 圆锥/圆柱/方体/球，
    按题目里的颜色和形状点击；单张约 0.1 秒，失败自动换图重试。
  · 验证码策略顺序：外接服务 → 离线识图 → 视觉模型 → OCR → 滑块 → 人工。
  · 元素截图支持 2 倍放大（clip.scale），提高小图识别率。

v1.1.1
  · 新增图形点选识别（数美“空间逻辑验证”，按文字找图形）：视觉模型看图返回坐标，
    按序点击并自动重试；可用内置样例图“测试识别”。
  · 新增 OCR 文字点选兜底（ddddocr 检测 + 分类，需自行安装）。
  · 验证码面板截图与题目文字会自动记录进日志，便于失败复盘。

v1.1.0
  · 新增“手动预置内容”模式（按顺序 / 随机，可在界面里维护列表）。
  · 验证码默认改为自动尝试：外接服务 → 滑块轨迹模拟 → 图片 OCR（可选 ddddocr）
    → 按次数与间隔重试 → 失败回退人工；新增 attempts / retry_interval /
    auto_slider / auto_ocr 配置。
  · 发帖失败自动重试（次数、间隔可配），可选择“重试时换内容”。
  · 发帖成功后用浏览器 Cookie 走 HTTP 旁路复核“我的帖子”页面（可关闭）。
  · 新增 iframe 内 contenteditable 填写兜底（CDP 与 Selenium 双引擎）。
  · 新增“记住账号密码”：Windows DPAPI 加密保存，默认关闭。
  · 发帖间隔下限由 30 秒调整为 10 秒（默认仍为 30 秒）。

v1.0.6
  · 外接验证码服务扩展点（默认人工接管）。
  · 登录/发帖全流程、结果检测、失败截图、日志落盘。

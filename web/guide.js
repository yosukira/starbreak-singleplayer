/* Bilingual, local-only product guide. Content follows the saved UI language. */
(function(){
  'use strict';
  const content={
    zh:{community:'<p><a href="https://discord.gg/4SC44Eff2f" target="_blank" rel="noopener noreferrer">SBRE Discord</a> · QQ 群：<strong>684496559</strong></p>',title:'使用说明',subtitle:'从第一场练习，到录像复盘与分享。',intro:'战斗在你的浏览器中运行。配装、按键和战绩保存在本机；只有主动分享的录像和提交的评论会发送到分享服务。',contents:'教程目录',language:'说明语言',configure:'配置一场战斗',records:'查看战斗记录',steps:[['选择场景与角色','打开“装备与设置”，选择地图、职业、MK 和装备。'],['保存并开始','确认地图和敌人参数，点击“保存并开始”，等待加载完成。'],['复盘这一场','结束后打开“战斗记录”，查看数据、回放，或生成分享码。']],sections:[
      {id:'start',title:'快速上手',intro:'第一次使用，按这三个步骤开始。',body:()=>''},
      {id:'setup',title:'配装与地图设置',intro:'所有练习参数都集中在“装备与设置”。',body:()=>`
        <div class="guide-columns"><div><h3>战斗配置</h3><ul><li>先选场景，再选 Wildfire、Duskwing、Fabricator 或 Ironclad，以及 MK 和植入体槽数。</li><li>点选装备槽，搜索物品名称或 ID，选择装备并调整实例词缀；最终属性由基础属性与词缀共同决定。</li><li>地图与敌人参数在配装旁边，可调整掉落、敌人血量、伤害、速度、刷怪节奏，以及场景专属参数。</li><li>“保存”保留当前配置；“保存并开始”保存并开启一场新战斗。地图、职业和弹体基础速度等开局设置，请在新一局确认效果。</li></ul></div>
        <div><h3>配置的保存与复用</h3><ul><li>“导出 / 导入”用于备份和搬运配装配置，与战绩里的录像导出分别使用。</li><li>“载入已保存配装”会重新读取已保存的内容；未保存的草稿请先保存或导出。</li><li>“恢复默认配置”用于重置练习配置，操作前建议先导出自己的方案。</li><li>标注“适配中”的地图暂不能启动。GY / EGY 支持炮台配置与跳过开局；支持的开场默认跳过。</li></ul></div></div>`},
      {id:'keys',title:'按键与画面缩放',intro:'在“装备与设置 → 按键与缩放”中修改并保存。下表是默认值。',body:()=>`
        <div class="guide-table-wrap"><table class="guide-table"><thead><tr><th>功能</th><th>默认按键</th><th>使用方式</th></tr></thead><tbody>
        <tr><td>打开 / 关闭编辑器</td><td><kbd>\u0060</kbd> 或 <kbd>F8</kbd></td><td>数字 1 左侧的键；也可点右上角“装备与设置”。</td></tr>
        <tr><td>重开当前局</td><td><kbd>R</kbd></td><td>默认长按 2 秒。</td></tr>
        <tr><td>切换职业</td><td><kbd>7</kbd> / <kbd>8</kbd> / <kbd>9</kbd> / <kbd>0</kbd></td><td>依次为 WF / DW / FAB / IC，默认长按 2 秒。</td></tr>
        <tr><td>放大画面 / 缩小视野</td><td><kbd>+</kbd></td><td>使用主键盘 + / = 键。</td></tr>
        <tr><td>缩小画面 / 扩大视野</td><td><kbd>−</kbd></td><td>使用主键盘减号键，默认每次调整 10%。</td></tr>
        <tr><td>互动</td><td>Auto</td><td>跟随游戏内互动绑定，也可指定练习器互动键。</td></tr>
        </tbody></table></div><p>长按时间、放大键、缩小键和每次缩放比例均可自定义。输入文字、修改数值或阅读说明时，不会触发练习器的重开与切职业快捷键。</p>
        <p class="guide-note">移动、跳跃、攻击和机动使用游戏本体 Controls 中的绑定；练习器快捷键与游戏内按键设置分别保存。画面缩放调整的是视野范围，浏览器页面缩放仍由浏览器控制。</p>`},
      {id:'history',title:'战绩与数据怎么看',intro:'打开“战斗记录 → 本地战绩”，先筛选，再进入详情。',body:()=>`
        <ul><li>支持日期、地图、职业、结果和来源筛选；可排序、选择多场并导出。</li><li>详情显示耗时、伤害、击杀、拾取、恢复生命、承受伤害，以及开局装备和参数；装备可逐件展开，也可全部展开 / 收起。</li><li>完全没有有效游戏操作的对局不会进入战绩。旧记录未采集的数据显示“—”，不能当作 0。</li><li>详情中的“筛选相同配装与参数”用于找相似练习；点弹窗外侧可关闭普通详情，全屏详情使用关闭按钮。</li></ul>
        <div class="guide-columns"><div class="guide-example"><h3>GY / EGY 炮台</h3><code>4(2)-7(2)-7(2)</code><p>顺序为前方、上方、下方；括号外是该位置炮台总数。前方括号是散射数量，上、下括号是追踪弹炮台数量。Gen 表示本场打掉的发生器数量。</p></div>
        <div class="guide-example"><h3>GY / EGY 耗时</h3><code>2:10 (2:30)</code><p>关闭跳过开局时，括号外从飞船停稳后计时，括号内是完整耗时。跳过开局只显示一个时间；旧录像缺少停稳数据时保留原耗时。</p></div></div>`},
      {id:'replay',title:'录像回放与接管',intro:'从战绩详情点击“回放本场”，在战斗页操作播放栏。',body:()=>`
        <p>“装备与设置 → 战斗配置”中的“录制录像”默认开启；关闭后新开的练习局不录制，已有录像和战绩保留。接管录像仍会保存新分支。</p>
        <ol><li><strong>回放：</strong>可暂停 / 继续，选择 0.25、0.5、1、2、4 倍速，或拖动时间条跳转。向后跳转需要从开局重建状态，复杂场景可能需要等待进度。</li><li><strong>接管：</strong>角色存活、状态校验通过且不在换房交接时，可点“接管”，也可按一个游戏实际接受的操作键。单纯点击鼠标不会自动接管。</li><li><strong>继续练习：</strong>接管后由你控制当前战场，另存一场战绩与录像，原录像保留。切到说明或战绩会暂停正在播放的录像；返回后可点“继续”。</li></ol>
        <p class="guide-note">“接管”置灰时，先等待加载或换房完成，并确认角色仍存活。兼容版本可尝试回放；状态不一致会停止，未经校验的版本不能接管。桌面和网页可以导入同一个 .sbreplay 文件。原生录像可在网页播放、校验后接管；接管录像也可返回客户端。旧录像缺少开局消息时需重新录制。</p>`},
      {id:'sharing',title:'分享码、最新与热门',intro:'客户端与网页版共用分享服务：最新分享、热门榜、分享码和评论互通。本地战绩各自保存；浏览别人的详情不等于导入。',body:()=>`
        <div class="guide-columns"><div><h3>分享自己的一场</h3><ol><li>进入详情，点击“生成分享码”。</li><li>按需填写英文 ID / 昵称；不填显示 Unknown。填写后，下次会自动带入。</li><li>确认生成，等待上传及服务器处理完成，复制分享码发给朋友。</li></ol><p>只有这一场的录像、开局配装与参数被上传；其他本地记录不会一起上传。昵称会随分享公开。</p></div>
        <div><h3>导入与发现</h3><ul><li>点击“输入分享码”，粘贴代码并等待下载、校验和导入完成。</li><li>也可导入 .sbreplay 文件；批量导出的 ZIP 用于搬运多场战绩。</li><li>别人分享的录像显示原分享码，不会再次上传成新的分享。</li><li>“最新分享”按分享时间排序；“近 7 天热门”按成功导入数排序，同一浏览器重复导入同一录像不重复计数。</li><li>两个分享榜都可筛选地图和职业；选 GY / EGY 后，可继续筛选前、上、下炮台数量，留空表示不限。</li></ul></div></div>`},
      {id:'comments',title:'分享录像的评论',intro:'有分享码的录像，使用同一个公开评论区。',body:()=>`
        <ol><li>打开分享录像的详情，找到评论区；本地未分享的录像需先生成分享码。</li><li>首次评论填写英文 ID，之后会在当前浏览器自动使用这个 ID。支持英文字符、数字、空格及 . _ -，最多 32 个字符；Unknown、null 等保留名称不能使用。</li><li>评论正文支持多语言，最多 1000 个字符。提交成功后，其他查看此分享码的人也能看到；评论较多时可加载更早内容。</li></ol>
        <p>昵称和评论均会校验格式与内容；未通过时请按提示修改。频繁提交可能需要稍候再试。</p>`},
      {id:'storage',title:'本地保存、语言与更新',intro:'同一浏览器、同一网址下保留自己的练习环境。',body:()=>`
        <div class="guide-columns"><div><h3>本机保存的内容</h3><ul><li>配装、按键、缩放设置、游戏选项和战绩保存在当前设备。换浏览器、换设备或换网址不会自动同步。</li><li>清除网站数据、使用无痕窗口或浏览器清理存储，会影响本地数据的保留。重要配装和录像请分别导出备份。</li><li>资源首次使用需要下载，之后优先复用本站缓存；新的地图可能还要补充下载。分享、评论和获取更新需要网络。</li></ul></div>
        <div><h3>语言与版本</h3><ul><li>首次按浏览器语言选择中文或英文；之后优先使用你保存的选择。说明页和“装备与设置”共用语言设置。</li><li>“简体中文（包含词缀）”还会翻译装备词缀；普通“简体中文”保留词缀原文。</li><li>编辑器中可查看版本并“检查更新”。本场结束后刷新页面获取新版，正在进行的战斗不会被自动强制重载。</li><li>网页和 Windows 玩家包分别发布；下载页会分别显示版本号和更新时间。桌面端仍使用固定更新通道。</li></ul></div></div>`},
      {id:'faq',title:'遇到问题时',intro:'先看当前阶段和提示，再决定下一步。',body:()=>`
        <div class="guide-faq"><details><summary>首次进入或切地图加载较慢</summary><p>查看加载阶段、百分比及资源数量。首次下载引擎、地图和音频可能较久；同一浏览器下再次使用通常更快。等待期间先完成加载，页面导航会暂时锁定，避免中途打断。</p></details>
        <details><summary>分享或导入失败</summary><p>确认网络与分享码正确，等待当前上传 / 下载完成后再试。失败不会删除原本的本地录像，可先导出备份；导入重复记录不会覆盖本地原记录。</p></details>
        <details><summary>录像不能接管，或提示版本不兼容</summary><p>确认角色存活，等待资源与房间交接完成。旧录像缺少必要数据，或与当前引擎状态不一致时，不能安全接管。先尝试兼容回放；缺少开局消息的旧原生录像需要重新录制。</p></details>
        <details><summary>找不到之前的配置或战绩</summary><p>确认仍在原来的设备、浏览器与网址。本站、localhost 和不同端口属于不同存储位置；如已导出备份，可从对应导入入口恢复。</p></details>
        <details><summary>鼠标位置、快捷键或视野不对</summary><p>先点击游戏画面恢复焦点，并检查游戏内 Controls 与练习器“按键与缩放”两套设置。浏览器缩放恢复 100% 后再检查；调整窗口后若仍异常，可在本场结束后刷新页面。</p></details></div>`}
    ]},
    en:{community:'<p><a href="https://discord.gg/4SC44Eff2f" target="_blank" rel="noopener noreferrer">SBRE Discord</a></p>',title:'Guide',subtitle:'Your first practice, then replays, review and sharing.',intro:'Battles run in your browser. Loadouts, keys and battle history stay on this device. Only replays you choose to share and comments you submit go to the sharing service.',contents:'CONTENTS',language:'Guide language',configure:'Set up a battle',records:'Open battle records',steps:[['Choose your encounter','Open Equipment & settings. Choose a map, class, MK and equipment.'],['Save & start','Check the encounter parameters, then select Save & start and wait for loading to finish.'],['Review your run','Open Battle records afterwards to inspect the result, watch a replay or create a sharing code.']],sections:[
      {id:'start',title:'Quick start',intro:'Three steps for your first practice.',body:()=>''},
      {id:'setup',title:'Loadouts & encounters',intro:'Equipment & settings brings the practice configuration together.',body:()=>`
        <div class="guide-columns"><div><h3>Battle setup</h3><ul><li>Select an encounter, then Wildfire, Duskwing, Fabricator or Ironclad, an MK and implant slots.</li><li>Select an equipment slot, search by item name or ID, choose an item and edit its modifiers. Final attributes combine the base item and its modifiers.</li><li>Encounter settings sit alongside the loadout: drops, enemy health, damage, speed, spawn pacing and map-specific options.</li><li>Save keeps the configuration. Save & start also starts a new battle. Check changes to starting settings, such as the map, class or base projectile speeds, in a new attempt.</li></ul></div>
        <div><h3>Keep and reuse a setup</h3><ul><li>Export / Import here moves loadout configurations. Replay exports have a separate entry in Battle records.</li><li>Reload saved reads the last saved setup. Save or export an unsaved draft before replacing it.</li><li>Restore defaults resets the practice configuration; export your own setup first if you want to keep it.</li><li>Encounters marked “in progress” cannot be started yet. GY / EGY have turret and opening options; supported openings are skipped by default.</li></ul></div></div>`},
      {id:'keys',title:'Keys & view zoom',intro:'Change and save these under Equipment & settings → Keys & zoom. These are the defaults.',body:()=>`
        <div class="guide-table-wrap"><table class="guide-table"><thead><tr><th>Action</th><th>Default</th><th>How it works</th></tr></thead><tbody>
        <tr><td>Open / close editor</td><td><kbd>\u0060</kbd> or <kbd>F8</kbd></td><td>The key left of 1, or use Equipment & settings.</td></tr>
        <tr><td>Restart this attempt</td><td><kbd>R</kbd></td><td>Hold for 2 seconds by default.</td></tr>
        <tr><td>Switch class</td><td><kbd>7</kbd> / <kbd>8</kbd> / <kbd>9</kbd> / <kbd>0</kbd></td><td>WF / DW / FAB / IC; hold for 2 seconds by default.</td></tr>
        <tr><td>Zoom in / narrower view</td><td><kbd>+</kbd></td><td>Use the main keyboard’s + / = key.</td></tr>
        <tr><td>Zoom out / wider view</td><td><kbd>−</kbd></td><td>Use the main keyboard’s minus key; default step is 10%.</td></tr>
        <tr><td>Interact</td><td>Auto</td><td>Follow the game’s interaction binding, or assign a practice key.</td></tr>
        </tbody></table></div><p>You can change the hold duration, zoom keys and zoom step. Typing in fields or reading this guide does not trigger the practice restart/class shortcuts.</p>
        <p class="guide-note">Movement, jumping, attacks and mobility use the original game’s Controls bindings. Those bindings and the practice shortcuts are saved separately. Game view zoom changes your field of view; browser page zoom is controlled by the browser.</p>`},
      {id:'history',title:'Reading battle history',intro:'Open Battle records → Local history, filter the list, then open Details.',body:()=>`
        <ul><li>Filter by date, map, class, result or source. Sort the list and select multiple battles for export.</li><li>Details show duration, damage, kills, pickups, healing, damage taken and starting equipment/parameters. Expand an item or use Expand all / Collapse all.</li><li>An attempt with no accepted gameplay input does not enter history. A dash means the old record did not collect that metric; it does not mean zero.</li><li>Find matching setup helps locate comparable runs. Click outside a normal detail sheet to dismiss it; use Close in full-screen mode.</li></ul>
        <div class="guide-columns"><div class="guide-example"><h3>GY / EGY turrets</h3><code>4(2)-7(2)-7(2)</code><p>The order is front, upper, lower. Outside each bracket is the total turret count. The front bracket counts scatter turrets; the upper/lower brackets count missile turrets. Gen counts destroyed generators.</p></div>
        <div class="guide-example"><h3>GY / EGY duration</h3><code>2:10 (2:30)</code><p>With the opening included, the first time begins when the ship settles. Brackets contain the full duration. Skipped openings show one time. Older replays without arrival timing keep their original duration.</p></div></div>`},
      {id:'replay',title:'Replays & takeover',intro:'Select Watch replay in battle details, then use the playback bar on the Battle tab.',body:()=>`
        <p>Record replays is on by default in Equipment & settings → Battle setup. Turning it off applies to new practice battles and keeps existing replays and history. Taking over a replay still saves a new branch.</p>
        <ol><li><strong>Watch:</strong> pause/resume, choose 0.25, 0.5, 1, 2 or 4 speed, or drag the timeline. Seeking backwards rebuilds the state from the start; complex scenes may take a moment.</li><li><strong>Take over:</strong> when the character is alive, state checks pass and no room handoff is underway, select Take over or press a gameplay key accepted by the game. A mouse click alone does not trigger automatic takeover.</li><li><strong>Continue practicing:</strong> control the current battle and save it as a separate run. The source replay stays unchanged. Switching to Guide or Battle records pauses replay playback; use Resume when you return.</li></ol>
        <p class="guide-note">If Take over is disabled, wait for loading or room transfer to finish and check that the character is alive. Compatible versions may attempt playback; a state mismatch stops it, and unverified versions cannot take over. Desktop and browser accept the same .sbreplay file. Native recordings can play and be taken over in the browser after verification; the branch can return to the native client. Older recordings without bootstrap messages need to be recorded again.</p>`},
      {id:'sharing',title:'Codes & shared replays',intro:'Desktop and web share the same latest/popular lists, sharing codes and comments. Local history stays on each device. Viewing details does not import a replay.',body:()=>`
        <div class="guide-columns"><div><h3>Share one of your battles</h3><ol><li>Open its details and select Create sharing code.</li><li>Optionally enter an English ID/nickname; blank displays Unknown. Your browser remembers it for next time.</li><li>Confirm, wait for upload and processing to finish, then copy the code to a friend.</li></ol><p>Only this replay and its starting setup/parameters are uploaded. Other local records stay on your device. Your chosen nickname is public with the replay.</p></div>
        <div><h3>Import and discover</h3><ul><li>Use Enter sharing code, paste a code and wait for downloading, validation and import.</li><li>You can also import a .sbreplay file. Batch ZIP exports move multiple battles together.</li><li>Someone else’s shared replay keeps its original sharing code instead of being uploaded as a new share.</li><li>Latest shares sorts by publication time. Popular this week ranks successful imports over seven days; repeated imports of the same replay in one browser do not add more counts.</li><li>Both lists support map and class filters. For GY / EGY, add exact front/upper/lower turret counts; leave them blank for any count.</li></ul></div></div>`},
      {id:'comments',title:'Comments on shared replays',intro:'A sharing code has one public comment thread.',body:()=>`
        <ol><li>Open the shared replay’s details and find Comments. A local, unshared replay needs a sharing code first.</li><li>Enter an English ID for your first comment. This browser remembers and reuses it. Use up to 32 English characters, numbers, spaces or . _ -. Reserved names such as Unknown and null are not allowed.</li><li>Comments support multiple languages and up to 1,000 characters. Once submitted, others viewing that code can read them. Load earlier comments in longer threads.</li></ol>
        <p>IDs and comments are checked for format and configured content rules. Follow the displayed message if an entry is rejected. Frequent submissions may require a short wait.</p>`},
      {id:'storage',title:'Local data, language & updates',intro:'Keep your practice environment in the same browser at the same address.',body:()=>`
        <div class="guide-columns"><div><h3>What stays on this device</h3><ul><li>Loadouts, keys, zoom settings, game options and battle history are stored here. They do not automatically sync to another browser, device or website address.</li><li>Clearing site data, private browsing and browser storage cleanup affect data retention. Export important loadouts and recordings separately as backups.</li><li>First use downloads resources; later visits reuse this site’s local cache. A different map may need more assets. Sharing, comments and fetching updates need a network connection.</li></ul></div>
        <div><h3>Language and versions</h3><ul><li>On first use, Chinese or English follows the browser language. Your saved choice takes priority afterwards. This guide and Equipment & settings share the same language preference.</li><li>简体中文（包含词缀） also translates item modifiers. The standard 简体中文 option keeps modifier text in English.</li><li>Find the version and Check for updates in the editor. Refresh after your battle to get a new web build; an active battle is not forcibly reloaded.</li><li>The website and Windows player are published separately. The download page shows both versions and update times; desktop installations keep using the fixed update channel.</li></ul></div></div>`},
      {id:'faq',title:'Troubleshooting',intro:'Read the current loading stage or message before deciding what to do next.',body:()=>`
        <div class="guide-faq"><details><summary>The first visit or a new map loads slowly</summary><p>Check the stage, percentage and resource counts. The first download of the engine, map and audio can take time; later attempts in the same browser are usually faster. Navigation is temporarily locked during loading to avoid interrupting initialization.</p></details>
        <details><summary>A share or import failed</summary><p>Check the connection and code. Let the current transfer finish before retrying. A failure does not delete your original local replay; export it as a backup if needed. Importing a duplicate does not overwrite the existing local record.</p></details>
        <details><summary>Takeover is unavailable or a version is incompatible</summary><p>Confirm that the character is alive and wait for resource loading and room handoff. A recording missing required data, or one whose state differs from the current engine, cannot safely take over. Try compatible playback first. Older native recordings missing bootstrap messages need to be recorded again.</p></details>
        <details><summary>My previous setup or history is missing</summary><p>Check the device, browser and website address. This website, localhost and different ports have separate storage. Restore an exported backup through its matching import entry.</p></details>
        <details><summary>Pointer, shortcut or view behavior seems wrong</summary><p>Click the game to restore focus and check both the original game’s Controls and the practice Keys & zoom settings. Reset browser page zoom to 100% and check again. If resizing still leaves a problem, refresh after the current battle.</p></details></div>`}
    ]}
  };
  const desktopContent={
    zh:{
      subtitle:'Windows 客户端：配装、练习、录像与分享。',
      intro:'战斗在 StarBreak 游戏客户端中运行；装备编辑、使用说明和战斗记录共用这个窗口。配装与战绩保存在当前安装目录，分享和评论需要联网。',
      steps:[['启动客户端','先确认 SBPE 能正常启动 StarBreak。将完整解压的 starbreak-singleplayer 文件夹放入 SBPE 目录，再双击 start.cmd。'],['配置并开始','在“装备与设置”选择地图、职业和配装，点击“保存并开始”。正常练习时会在游戏内重新开局，不需要关闭再启动游戏。'],['查看与复盘','打开“战斗记录”查看详情和录像；原生录像在游戏窗口播放，符合接管条件时按游戏操作键继续练习。']],
      sections:{
        keys:{title:'游戏按键与练习快捷键',intro:'在“装备与设置 → 按键设置”中修改练习快捷键；下表是默认值。',body:()=>`
          <div class="guide-table-wrap"><table class="guide-table"><thead><tr><th>功能</th><th>默认按键</th><th>使用方式</th></tr></thead><tbody>
          <tr><td>显示 / 隐藏装备与战绩窗口</td><td><kbd>\u0060</kbd> 或 <kbd>F8</kbd></td><td>回到游戏窗口按键；\u0060 是数字 1 左侧的键。</td></tr>
          <tr><td>重开当前局</td><td><kbd>R</kbd></td><td>默认长按 2 秒。</td></tr>
          <tr><td>切换 WF / DW / FAB / IC</td><td><kbd>7</kbd> / <kbd>8</kbd> / <kbd>9</kbd> / <kbd>0</kbd></td><td>默认长按 2 秒，在游戏内切换职业并重新开局。</td></tr>
          <tr><td>互动 / 开 Gen</td><td>Auto</td><td>跟随游戏本体互动绑定，支持键盘和手柄；也可指定练习器的键盘互动键。</td></tr>
          </tbody></table></div><p>长按时间可改为 0.5–5 秒。移动、跳跃、攻击、机动和手柄绑定在原游戏的 Controls 中设置。练习快捷键与原游戏绑定分别保存，按键操作时请让游戏窗口获得焦点。</p>`},
        replay:{intro:'在战绩详情点击“回放本场”；Windows 原生录像会在原游戏窗口播放。',body:()=>`
          <p>“战斗配置 → 录制录像”默认开启。关闭后新开的练习不录制，已有战绩与录像保留；接管仍会保存新分支。修改录制开关后开始新局时会重新初始化游戏。</p>
          <ol><li><strong>播放：</strong>打开原生录像后回到游戏窗口观看；需要暂停、倍速、跳转或从头播放时，回到装备与战绩窗口的“战斗”页操作播放栏。原生游戏底部目前没有单独的进度条。</li><li><strong>接管：</strong>角色存活且校验通过后，在游戏窗口按一个实际生效的游戏操作键，或在播放栏点击“接管”。鼠标点击本身不会自动接管。</li><li><strong>继续练习：</strong>接管会另存战绩与录像，原记录保留。结束复盘后可从“装备与设置”保存并开始新局；从回放返回全新练习会重新初始化游戏。</li></ol>
          <p class="guide-note">客户端与网页可导入同一个 .sbreplay 文件；原生录像在游戏客户端播放，导入的网页录像使用窗口内的网页引擎播放。能否回放和接管取决于录像版本及状态校验。统计记录可能没有录像；缺少开局数据或版本不兼容的旧录像需重新录制。</p>`},
        storage:{intro:'本地数据跟随这份安装保存，换一份目录不会自动同步。',body:()=>`
          <div class="guide-columns"><div><h3>保存与备份</h3><ul><li>自己的配装、地图参数、练习按键、战绩、录像和日志保存在 userdata 文件夹中。重新解压完整包时请保留这份文件夹。</li><li>配装使用编辑器的“导出 / 导入”；录像使用战斗记录里的导出 / 导入。也可在关闭模拟器后备份整个 userdata。</li><li>本地练习不依赖网页服务；首次准备编辑窗口组件、检查更新、分享、导入分享码和评论需要网络。</li></ul></div>
          <div><h3>语言与更新</h3><ul><li>使用说明和编辑器共用语言选择；中文版显示 Discord 与 QQ 群，英文版显示 Discord。</li><li>“简体中文（包含词缀）”会翻译词缀；普通“简体中文”保留词缀原文。</li><li>编辑器中可以查看版本并“检查更新”。退出游戏及启动器后再次运行 start.cmd 应用更新；进行中的对局不会被强制更新。</li><li>更新后日常切图、切职业和修改配装使用游戏内重开，不需要每次退出游戏。</li></ul></div></div>`},
        faq:{intro:'先看窗口连接提示和启动器输出，再检查对应项目。',body:()=>`
          <div class="guide-faq"><details><summary>装备编辑器没有出现</summary><p>先回到游戏按 \u0060 或 F8。仍未显示时，双击 tools/open-editor.cmd 单独打开窗口。启动状态在 userdata/desktop-records/window-status.json，详细日志在 userdata/logs 下最近一场的 gear-editor.log。</p></details>
          <details><summary>提示找不到客户端，或玩家包文件缺失</summary><p>先确认同一份 SBPE 本身能启动 StarBreak，config.ini 指向正确的游戏目录。完整解压 Windows 玩家包并将整个项目文件夹放在 SBPE 内；不要只复制 start.cmd，也不要用 GitHub 的 Source code 包代替玩家包。</p></details>
          <details><summary>怪物不受伤、炮台不索敌或 Gen 无反应</summary><p>检查“装备与设置”顶部的游戏反馈状态。若未连接或提示组件异常，关闭游戏后从 start.cmd 重新启动，并检查本次日志。反馈正常时，确认发生器已进入可互动阶段；检查游戏 Controls 的互动键或手柄绑定。</p></details>
          <details><summary>回放不能接管</summary><p>先让游戏窗口获得焦点，确认角色存活，等待资源加载或换房交接完成。状态不一致或版本不兼容时会停止回放并禁用接管；缺少必要数据的旧录像需要重新录制。</p></details>
          <details><summary>配置或战绩不见了，分享失败</summary><p>确认启动的是原来的安装目录，userdata 仍在；配装与录像分别从对应入口导入备份。分享或分享码导入失败时检查网络与提示，等待传输完成后再试；失败不会删除原本的本地录像。</p></details></div>`}
      }
    },
    en:{
      subtitle:'Windows client: equipment, practice, replays and sharing.',
      intro:'Battles run in the StarBreak game client. Equipment, this guide and battle records share this window. Loadouts and history belong to this installation; sharing and comments need a connection.',
      steps:[['Launch the client','First confirm that SBPE can launch StarBreak. Extract the complete player package into a starbreak-singleplayer folder inside SBPE, then run start.cmd.'],['Configure and start','Choose a map, class and equipment in Equipment & settings, then select Save and start. Normal practice changes reload the battle in-game without closing the game process.'],['Review your practice','Open Battle records for details and replays. Native recordings play in the game window; press an accepted gameplay key when takeover is ready to continue practicing.']],
      sections:{
        keys:{title:'Game controls & practice shortcuts',intro:'Change practice shortcuts in Equipment & settings → Keys. These are the defaults.',body:()=>`
          <div class="guide-table-wrap"><table class="guide-table"><thead><tr><th>Action</th><th>Default</th><th>How to use</th></tr></thead><tbody>
          <tr><td>Show / hide equipment and records</td><td><kbd>\u0060</kbd> or <kbd>F8</kbd></td><td>Press in the game window; \u0060 is the key beside 1.</td></tr>
          <tr><td>Restart the current practice</td><td><kbd>R</kbd></td><td>Hold for 2 seconds.</td></tr>
          <tr><td>Switch WF / DW / FAB / IC</td><td><kbd>7</kbd> / <kbd>8</kbd> / <kbd>9</kbd> / <kbd>0</kbd></td><td>Hold for 2 seconds to restart in-game as that class.</td></tr>
          <tr><td>Interact / open a Gen</td><td>Auto</td><td>Follow the original keyboard/controller interaction binding, or assign a practice keyboard key.</td></tr>
          </tbody></table></div><p>The hold duration is adjustable from 0.5 to 5 seconds. Set movement, jump, attacks, mobility and controller bindings in the original game's Controls. Practice shortcuts are saved separately. Focus the game window before using gameplay keys.</p>`},
        replay:{intro:'Select Watch replay in battle details. Windows native recordings play in the original game window.',body:()=>`
          <p>Record replays is enabled by default under Battle setup. Disabling it stops recording new practices and preserves existing history and recordings. Takeover still saves a new branch. Starting a battle after changing this switch reinitializes the game.</p>
          <ol><li><strong>Watch:</strong> return to the game window for native playback. Use the Battle tab in this equipment/records window for pause, speed, seeking or replaying from the beginning. There is currently no separate timeline at the bottom of the native game.</li><li><strong>Take over:</strong> with a living character and a verified state, press an accepted gameplay key in the game window, or select Take over on the playback bar. A mouse click alone does not take over.</li><li><strong>Continue practicing:</strong> takeover saves a separate battle and recording, preserving the source. Use Equipment & settings → Save and start for a fresh practice after replay; this reinitializes the game.</li></ol>
          <p class="guide-note">Desktop and web import the same .sbreplay files. Native recordings play in the game client; imported browser recordings use the embedded browser engine. Playback and takeover require compatible versions and state checks. Some statistics have no recording. Older recordings missing bootstrap data must be recorded again.</p>`},
        storage:{intro:'Local data belongs to this installation and does not automatically follow a different folder.',body:()=>`
          <div class="guide-columns"><div><h3>Saving and backups</h3><ul><li>Your loadouts, map parameters, practice keys, history, recordings and logs are stored in userdata. Keep this folder when extracting a complete replacement package.</li><li>Use the editor's Export / Import for equipment and Battle records export / import for recordings. You can also close the simulator and back up the entire userdata folder.</li><li>Local practice does not require the website. First-time editor component preparation, update checks, sharing, sharing codes and comments require a connection.</li></ul></div>
          <div><h3>Language and updates</h3><ul><li>This guide and the editor use the same language preference. Chinese shows Discord and the QQ group; English shows Discord.</li><li>简体中文（包含词缀） also translates item modifiers; the standard Chinese option retains modifier text in English.</li><li>Use Check for updates in the editor. Close the game and launcher, then run start.cmd again to apply updates. An active battle is not forcibly updated.</li><li>Normal map, class and equipment changes restart the practice in-game, without relaunching the game process.</li></ul></div></div>`},
        faq:{intro:'Check the connection notice and launcher output first.',body:()=>`
          <div class="guide-faq"><details><summary>The equipment window did not appear</summary><p>Return to the game and press \u0060 or F8. If it remains hidden, run tools/open-editor.cmd to open it separately. Startup status is in userdata/desktop-records/window-status.json; details are in the latest userdata/logs session's gear-editor.log.</p></details>
          <details><summary>Client not found or player files missing</summary><p>Confirm that this SBPE installation can launch StarBreak and its config.ini points to the correct game directory. Extract the complete Windows player package inside SBPE. Copying only start.cmd, or using GitHub's Source code package, does not provide the player runtime.</p></details>
          <details><summary>Enemies ignore damage, turrets do not aim, or a Gen will not open</summary><p>Check game feedback in Equipment & settings. If disconnected or unavailable, close the game, restart through start.cmd and inspect this session's logs. With healthy feedback, wait for the generator's interaction stage and check keyboard/controller bindings in the game's Controls.</p></details>
          <details><summary>Replay takeover is unavailable</summary><p>Focus the game window, confirm the character is alive and wait for resources or room handoff. State or version mismatches stop playback and disable takeover. Older recordings missing required data must be recorded again.</p></details>
          <details><summary>Missing local data or failed sharing</summary><p>Check that you launched the original installation and retained its userdata folder. Import equipment and replay backups through their respective entries. For sharing/code failures, check the connection and wait for the transfer; failures do not delete your original local replay.</p></details></div>`}
      }
    }
  };
  function guideContent(language,desktop=false){
    const key=language.startsWith('zh')?'zh':'en',base=content[key];
    if(!desktop)return base;
    const copy=desktopContent[key];
    return {...base,...copy,sections:base.sections.map(section=>{
      const result={...section,...copy.sections[section.id]};
      if(['sharing','comments'].includes(section.id))result.body=()=>section.body().replaceAll('浏览器','客户端').replaceAll('browser','installation');
      return result;
    })};
  }
  class BattleGuide{
    constructor(root,language,actions={}){
      this.root=root;this.language=language;this.actions=actions;
      root.onclick=event=>{const button=event.target?.closest?.('[data-guide-action]');if(button)this.actions[button.dataset.guideAction]?.();};
      root.onchange=event=>{if(event.target?.matches?.('[data-guide-language]'))this.actions.language?.(event.target.value);};
    }
    render(){
      const language=this.language(),copy=guideContent(language,this.actions.desktop);
      this.root.setAttribute('aria-label',copy.title);
      this.root.innerHTML=`<div class="guide-heading"><div><span class="guide-eyebrow">STARBREAK / FIELD MANUAL</span><h1>${copy.title}</h1><p class="guide-subtitle">${copy.subtitle}</p><p>${copy.intro}</p>${copy.community}</div><label class="guide-language">${copy.language}<select data-guide-language aria-label="${copy.language}">${[['zh','简体中文'],['zh-affixes','简体中文（包含词缀）'],['en','English']].map(([value,label])=>`<option value="${value}" ${value===language?'selected':''}>${label}</option>`).join('')}</select></label></div>
        <div class="guide-layout"><aside class="guide-contents"><h2>${copy.contents}</h2><nav aria-label="${copy.contents}">${copy.sections.map((section,i)=>`<a href="#guide-${section.id}"><span>${String(i+1).padStart(2,'0')}</span>${section.title}</a>`).join('')}</nav></aside>
        <div class="guide-chapters">${copy.sections.map((section,i)=>`<section id="guide-${section.id}" class="guide-chapter" aria-labelledby="guide-title-${section.id}"><div class="guide-chapter-title"><span>${String(i+1).padStart(2,'0')}</span><h2 id="guide-title-${section.id}">${section.title}</h2></div><p class="guide-lead">${section.intro}</p>${section.id==='start'?`<ol class="guide-steps">${copy.steps.map(([title,description])=>`<li><h3>${title}</h3><p>${description}</p></li>`).join('')}</ol><div class="guide-actions"><button class="primary" data-guide-action="configure">${copy.configure}</button><button class="secondary" data-guide-action="records">${copy.records}</button></div>`:section.body()}</section>`).join('')}</div></div>`;
    }
  }
  if(typeof module!=='undefined')module.exports={content,BattleGuide,guideContent};
  else globalThis.BattleGuide=BattleGuide;
})();

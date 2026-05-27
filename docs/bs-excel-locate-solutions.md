# B/S 环境 Excel 定位方案

> 当前 `win32com` 方案仅在 Streamlit 与 Excel 同机运行时有效。
> 真实 B/S 部署后，服务器无法操作用户本机 Excel，需改用以下方案。

---

## 方案一：下载带标记的 Excel（最简单）

**原理**：服务端用 openpyxl 在导出文件中预设高亮/批注，用户下载后打开即可看到标记。

**实现要点**：
```python
import openpyxl
from openpyxl.comments import Comment
from openpyxl.styles import PatternFill, Font

wb = openpyxl.load_workbook("output.xlsx")
ws = wb["参数输入表"]

# 黄色高亮
ws["I250"].fill = PatternFill("solid", fgColor="FFFF00")
ws["I250"].font = Font(bold=True)

# 批注说明
ws["I250"].comment = Comment("变化量: +123.45 (来自快照对比)", "System")

wb.save("output_marked.xlsx")
```

**优点**：
- 零前端开发，openpyxl 已在项目中使用
- 跨平台，Linux 服务器可运行

**缺点**：
- 每次定位都要重新下载文件，效率低
- 无法实时跳转到指定单元格
- 用户需要手动找到高亮位置

**适用场景**：MVP 阶段、低频操作

---

## 方案二：Web 在线电子表格（中等复杂度）

**原理**：用前端电子表格组件在浏览器内直接渲染 Excel，支持单元格高亮和滚动定位。

**可选组件**：

| 组件 | 开源 | 特点 |
|------|------|------|
| [Luckysheet](https://mengshukeji.gitee.io/LuckysheetDocs/) | 是 | 国产，功能全面，支持公式 |
| [Univer](https://univer.ai/) | 是 | Luckysheet 团队新作，架构更现代 |
| [OnlyOffice](https://www.onlyoffice.com/) | 是 | 企业级，支持协作编辑 |
| [Handsontable](https://handsontable.com/) | 商业 | 数据网格，非完整电子表格 |

**实现要点（以 Luckysheet 为例）**：
```javascript
// 前端初始化
luckysheet.create({
    container: 'luckysheet',
    data: excelData,  // 从后端 API 获取
    showtoolbar: false,
    showsheetbar: true,
});

// 定位到指定单元格
function locateCell(sheetName, cellRef) {
    const sheetIndex = getSheetIndex(sheetName);
    luckysheet.setSheetActive(sheetIndex);

    // 解析单元格引用 (如 "I250")
    const { row, col } = parseCellRef(cellRef);

    // 高亮
    luckysheet.setCellFormat(row, col, 'bg', '#FFFF00');

    // 滚动到目标位置
    luckysheet.scroll({
        targetRow: row,
        targetCol: col,
    });
}
```

**后端配合**：
```python
# 新增 API：将 Excel 转为 Luckysheet 格式
@app.get("/api/excel/{task_id}")
def get_excel_data(task_id: str):
    wb = openpyxl.load_workbook(excel_path)
    # 转换为 Luckysheet JSON 格式
    return convert_to_luckysheet_format(wb)
```

**优点**：
- 完全在浏览器内操作，无需下载
- 支持实时高亮和跳转
- 可与现有 Streamlit 页面共存（用 components.html 嵌入）

**缺点**：
- 大文件（58K 单元格）渲染性能需要测试
- 前端开发量较大
- 公式计算可能与 Excel 不完全一致

**适用场景**：需要频繁定位、在线查看的场景

---

## 方案三：Office Add-in 插件（最佳体验，高复杂度）

### 3.1 架构概览

Office Add-in 是嵌入在 Excel 内部的网页面板，用 HTML/JS/React 开发，可调用 Excel JavaScript API 操作工作簿。

```
用户浏览器                          服务器
   │                                  │
   │  Streamlit 页面展示分析结果       │
   │  （图谱、对比、传播链等）          │
   │                                  │
   │  ┌─────────────────────┐         │
   │  │  用户本机 Excel       │         │
   │  │  ┌───────────────┐  │         │
   │  │  │ Add-in 侧边栏 │  │         │
   │  │  │  ←→ 服务器 API │  <────────│
   │  │  └───────────────┘  │         │
   │  │  JS 调用 Excel API  │         │
   │  │  高亮/跳转/读数据    │         │
   │  └─────────────────────┘         │
```

### 3.2 通信方式

#### 方式 A：轮询（最简单，延迟 1-3 秒）

```javascript
// Add-in 端：定时查询
setInterval(async () => {
    const res = await fetch('/api/locate-queue?user=xxx');
    const cmd = await res.json();
    if (cmd.pending) {
        await Excel.run(async (ctx) => {
            const sheet = ctx.workbook.worksheets.getItem(cmd.sheetName);
            const range = sheet.getRange(cmd.cellRef);
            range.format.fill.color = "yellow";
            range.select();
            await ctx.sync();
        });
    }
}, 2000);
```

#### 方式 B：WebSocket（实时性好）

```javascript
// Add-in 端：WebSocket 监听
const ws = new WebSocket('wss://server/ws/locate?token=xxx');
ws.onmessage = async (event) => {
    const cmd = JSON.parse(event.data);
    // cmd = { sheetName: "参数输入表", cellRef: "I250", message: "变化量: +123" }

    await Excel.run(async (ctx) => {
        const sheet = ctx.workbook.worksheets.getItem(cmd.sheetName);
        const range = sheet.getRange(cmd.cellRef);
        range.format.fill.color = "yellow";
        range.select();
        await ctx.sync();
    });
};

// Streamlit 端：触发定位
# 用户在网页点击"定位"
import websockets
await websocket.send(json.dumps({
    "sheetName": "参数输入表",
    "cellRef": "I250",
    "message": "变化量: +123"
}))
```

#### 方式 C：Add-in 自包含（最佳架构）

不依赖 Streamlit 触发，用户直接在 Excel 侧边栏内完成所有操作。

```javascript
// Add-in 侧边栏 UI
function App() {
    const [indicators, setIndicators] = useState([]);

    // 加载指标列表
    useEffect(() => {
        fetch('/api/indicators?task=xxx').then(r => r.json()).then(setIndicators);
    }, []);

    // 点击指标 → 读取关联单元格 → 高亮定位
    const handleLocate = async (indicator) => {
        const cells = await fetch(`/api/indicator/${indicator.id}/cells`).then(r => r.json());

        await Excel.run(async (ctx) => {
            for (const cell of cells) {
                const sheet = ctx.workbook.worksheets.getItem(cell.sheet);
                const range = sheet.getRange(cell.ref);
                range.format.fill.color = "yellow";
            }
            // 跳转到第一个单元格
            const firstSheet = ctx.workbook.worksheets.getItem(cells[0].sheet);
            firstSheet.getRange(cells[0].ref).select();
            await ctx.sync();
        });
    };

    return (
        <div>
            <h3>指标浏览器</h3>
            {indicators.map(ind => (
                <button onClick={() => handleLocate(ind)}>{ind.name}</button>
            ))}
        </div>
    );
}
```

### 3.3 开发步骤

1. **脚手架**：`yo office`（Microsoft 官方生成器，支持 React/TypeScript）
2. **清单文件** (`manifest.xml`)：定义 Add-in 名称、权限、服务器 URL
3. **侧边栏页面**：React 应用，调用后端 API 获取图谱数据
4. **Excel JS API**：操作工作簿（读单元格、设格式、选中区域）
5. **后端 API**：从现有 `financial_kg` 模块暴露 REST API
6. **部署**：Add-in 页面部署到 HTTPS 服务器，用户通过 manifest.xml 安装

### 3.4 技术栈

| 层 | 技术 |
|----|------|
| Add-in 前端 | React + TypeScript + Office.js |
| Add-in 打包 | webpack / Vite |
| 后端 API | FastAPI（包装现有 financial_kg） |
| 通信 | REST API + WebSocket（可选） |
| 认证 | OAuth 2.0 / API Token |

**优点**：
- 用户体验最好，在 Excel 内直接操作
- 实时高亮、跳转，无需下载文件
- 可逐步把 Streamlit 功能迁移过来

**缺点**：
- 前端开发量最大（独立 React 应用）
- 需要用户安装 Add-in
- 需要后端 API 改造（从 Streamlit 脚本变为 REST API）
- 仅支持 Excel 2016+ / Excel Online

**适用场景**：产品化阶段、专业用户、高频使用

---

## 方案四：协议链接（中等复杂度）

**原理**：利用 `ms-excel:` 或自定义 URI 协议，浏览器唤起本地 Excel 并打开指定文件。

**自定义协议注册（Windows）**：
```
Windows Registry:
[HKEY_CLASSES_ROOT\finlocate]
@="URL:Financial Locate Protocol"
"URL Protocol"=""

[HKEY_CLASSES_ROOT\finlocate\shell\open\command]
@="\"C:\\Tools\\FinLocate.exe\" \"%1\""
```

**协议链接格式**：
```
finlocate://open?file=\\server\share\output.xlsx&sheet=参数输入表&cell=I250&msg=变化+123
```

**客户端工具（FinLocate.exe）**：
```python
# 用 Python 打包为 exe（PyInstaller）
import sys, win32com.client

def main():
    url = sys.argv[1]
    # 解析 finlocate://open?file=...&sheet=...&cell=...
    params = parse_url(url)

    xl = win32com.client.Dispatch("Excel.Application")
    xl.Visible = True
    wb = xl.Workbooks.Open(params["file"])
    ws = wb.Sheets(params["sheet"])
    ws.Activate()
    ws.Range(params["cell"]).Select()

if __name__ == "__main__":
    main()
```

**Streamlit 端触发**：
```python
import urllib.parse

file_path = "\\\\server\\share\\output.xlsx"
params = urllib.parse.urlencode({
    "file": file_path,
    "sheet": "参数输入表",
    "cell": "I250",
    "msg": "变化量: +123.45"
})
st.markdown(f"[📋 在 Excel 中定位](finlocate://open?{params})")
```

**优点**：
- 开发量较小，只需一个小的客户端工具
- 实时定位体验好
- 与现有 Streamlit 页面配合自然

**缺点**：
- 需要每台客户端安装/注册协议（IT 部署）
- 仅 Windows 有效
- 需要文件共享路径（\\server\share）对用户可访问
- 安全性需要额外处理（防止恶意链接）

**适用场景**：内网环境、可控部署、Windows 生态

---

## 方案对比总结

| 维度 | 下载标记 Excel | Web 电子表格 | Office Add-in | 协议链接 |
|------|:-:|:-:|:-:|:-:|
| 开发量 | 低 | 中高 | 高 | 中 |
| 定位实时性 | 差 | 好 | 好 | 好 |
| 用户体验 | 一般 | 好 | 最好 | 好 |
| 跨平台 | 是 | 是 | 否（仅 Excel） | 否（仅 Windows） |
| 依赖安装 | 无 | 无 | 需安装 Add-in | 需注册协议 |
| 大文件性能 | 好 | 需测试 | 好 | 好 |
| 推荐阶段 | MVP | 成长期 | 产品化 | 内网 |

**建议路线**：
1. **MVP**：方案一（下载标记 Excel），改动最小
2. **内网部署**：方案四（协议链接），体验好、开发可控
3. **产品化**：方案三方式 C（Add-in 自包含），最终形态

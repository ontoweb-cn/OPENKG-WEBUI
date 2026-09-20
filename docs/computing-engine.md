# Computing Engine 实现机制分析

> 代码库：OpenSPG（`/Users/simon/project/openspgapp`）

## 1. 核心抽象接口

`ComputingEngineClient` 定义了三大能力：

- `submitBuilderJob`（提交图构建任务）
- `queryStatus`（查询任务状态）
- `stop`（停止任务）

所有后端引擎都实现该接口。

## 2. 驱动加载 / 选择机制（JDBC 风格 DriverManager 工厂）

`ComputingEngineClientDriverManager`：

- 静态块中通过 `DriverManagerUtils.loadDrivers(...)` 加载实现类；
- `getClient(connUrl)` 解析 URL 的 **scheme**，遍历已注册 driver，找到 `driver.acceptsConfig(scheme)` 返回 true 的那个，调用 `connect(connUrl)` 创建 client。

引擎的选择**完全由连接 URL 的 scheme 决定**，scheme 即驱动选择键。

## 3. 配置位置

配置项：`cloudext.computingengine.url`

- 注入点：`DefaultValue.computingEngineUrl`（`@Value("${cloudext.computingengine.url:}")`）
- 使用点：调度任务 `ComputingEngineAsyncTask` 将 `value.getComputingEngineUrl()` 传给 `ComputingEngineClientDriverManager.getClient(...)`
- 配置样例（dev）：`cloudext.computingengine.url=aistudio://aistudio.alipay.com`

## 4. 已实现的后端引擎

### 4.1 Local（`local://`）

`LocalComputingEngineClientDriver`：

- `driverScheme()` 返回 `"local"`；
- 实现方式：**子进程 + 内存/磁盘进程注册表**，在 openspg server 容器内执行构建命令（KAG_COMMAND），实现"异步执行 + 状态轮询"；
- 加载方式：通过 SPI 机制自动发现（静态块）。

### 4.2 AIStudio（`aistudio://`）

模块：`cloudext/impl/computing-engine/aistudio`（独立的顶层模块）。

`AiStudioClientDriver`：

- `driverScheme()` 返回 `"aistudio"`；
- `connect(url)` 直接 `new AiStudioClient(url)`。

**注册方式差异**：`AiStudioClientDriver` 只在自身 `static{}` 块 `registerDriver`，**必须显式触发一次类加载**，未走 SPI 自动加载。注册点在 `StartupConfig` 启动逻辑中调用 `new AiStudioClientDriver();`。

`AiStudioClient` 通过 `AIStudioWorkflowClient`（`com.alipay.aistudio.workflow`）以 HTTPS 调用远端 KAG/AIStudio 平台：

- **连接**：从 connUrl 解析 host 拼成 `https://host` 端点，取 query 里的 `token` 作鉴权令牌；
- **提交构建**（`submitBuilderJob`）：把 `BuilderJob` 的 extension 装配成 `TrainingJob`（TensorFlow 运行时、worker 的 cpu/gpu/mem/disk 资源）→ `client.execute(aiContext, job, dimaId)` 提交到远端，返回远端 `taskId` 与日志 URL。KAG 的执行在远端 AIStudio 容器内跑构建命令（KAG_COMMAND / jar）；
- **查询状态**（`queryStatus`）：`client.queryTaskStatus(id)`，把远端 phase（SUBMIT/PREPARING/INIT/QUEUED/RUNNING/SUCCESS/FAILED/STOP_RUNNING…）映射为统一 `ComputingStatusEnum`；
- **停止**（`stop`）：`client.stopTask(...)`。

## 5. 完整选择链路

```
cloudext.computingengine.url = aistudio://aistudio.alipay.com   (application-dev.properties)
  → DefaultValue.computingEngineUrl (@Value 注入)
  → ComputingEngineAsyncTask: getClient(url)                     (scheduler 提交/查状态)
  → ComputingEngineClientDriverManager.getClient(url)
       按 URL scheme=="aistudio" 匹配 AiStudioClientDriver        (StartupConfig 已注册)
  → new AiStudioClient(url)  → HTTP 调远端 KAG/AIStudio 工作流
```

## 6. 小结

- `aistudio://`：**远端托管执行**。OpenSPG 侧只是"任务提交/轮询/停止"的薄客户端（driver 需手动注册），真正的图构建/KAG 计算在 AIStudio 平台的容器里执行；
- `local://`：**容器内子进程执行**；
- 两者通过同一套 scheme→driver 匹配机制并存，切换引擎只需修改 URL 前缀。

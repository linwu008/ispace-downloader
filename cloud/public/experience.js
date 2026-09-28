/* Shared, side-effect-free presentation rules. Never infer progress from a heartbeat. */
(function (root) {
  const activeStatuses = [
    "awaiting_confirmation",
    "queued",
    "running",
    "canceling",
  ];
  const savedStatuses = ["downloaded", "existing", "skipped"];
  function semester(date = new Date()) {
    const parts = new Intl.DateTimeFormat("en", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "numeric",
    }).formatToParts(date);
    const year = Number(parts.find((p) => p.type === "year").value),
      month = Number(parts.find((p) => p.type === "month").value);
    const start = month >= 9 ? year : year - 1;
    return `${start}–${start + 1} / ${month >= 9 || month === 1 ? "第一学期" : "第二学期"}`;
  }
  function connection(device, stale = false, now = Date.now() / 1000) {
    if (stale)
      return {
        title: "连接状态待刷新",
        detail:
          "网站暂时无法取得最新状态；下面保留的是上次成功获取的信息。请检查网络后重试。",
      };
    if (!device)
      return {
        title: "尚未配对",
        detail: "在 Windows 电脑下载并启动助手，再到“我的设备”生成配对码。",
      };
    const s = device.snapshot || {},
      r = s.readiness;
    if (!device.online || now - device.last_seen >= 75)
      return {
        title: "暂未收到电脑心跳",
        detail:
          "无法确定是电脑休眠、助手未运行还是网络中断。请在该电脑检查助手是否运行及联网情况。",
      };
    if (s.paused)
      return {
        title: "助手已暂停",
        detail: "请在电脑助手中恢复运行。网站已保存的任务会继续保留。",
      };
    if (s.auth === "auth_required" || (r && !r.auth))
      return {
        title: "请连接学校账号",
        detail: "电脑已连接官网，请在助手设置中重新登录学校账号。",
      };
    if (!r || now - r.at >= 90 || r.at > now + 10)
      return {
        title: "电脑在线 · 等待就绪检查",
        detail: "已收到连接消息；学校登录和目录可用性尚未确认。",
      };
    if (r.busy)
      return {
        title: "电脑正在处理任务",
        detail: "可在任务记录查看当前状态；新任务将等待电脑空闲。",
      };
    const added = (s.courses || []).filter(
      (c) => c.membership === "added" && c.enabled,
    );
    if (!added.length)
      return {
        title: "电脑在线 · 待选择课程",
        detail: "前往“我的课程”刷新学校列表并添加课程。",
      };
    if (added.some((c) => !c.bound || !r.courses?.includes(c.id)))
      return {
        title: "电脑在线 · 请检查课程目录",
        detail:
          "部分课程尚未授权目录，或目录当前不可用。请在电脑助手设置中检查。",
      };
    return {
      title: "电脑已准备好",
      detail: "可以提交同步任务。手动任务需确认后由电脑执行，文件保存在电脑。",
    };
  }
  function task(job, now = Date.now() / 1000) {
    const age = Math.max(0, now - (job.updated || job.created));
    const detail =
      {
        awaiting_confirmation: "等待用户确认；电脑就绪后才可开始。",
        queued: "已保存到网站，等待助手领取。",
        running: "助手已领取，正在执行；当前助手不提供逐文件实时进度。",
        canceling: "已请求取消，等待助手停止；已下载文件会保留。",
        success: "助手已报告完成。",
        partial: "部分完成，请检查失败项后重试。",
        failed: "执行失败，请查看原因后重试。",
        auth_required: "请在电脑助手中重新登录学校账号。",
        canceled: "任务已取消，已下载文件保留。",
      }[job.status] || "等待状态更新。";
    return {
      detail,
      stalled: ["running", "canceling"].includes(job.status) && age > 600,
      counts:
        ["success", "partial", "failed"].includes(job.status) &&
        job.kind === "sync"
          ? `下载 ${job.result?.downloaded || 0} · 跳过 ${job.result?.skipped || 0} · 失败 ${job.result?.failed || 0}`
          : "",
    };
  }
  function error(e) {
    if (
      ["TimeoutError", "AbortError"].includes(e?.name) ||
      /signal timed out|fetch failed|failed to fetch|networkerror/i.test(
        e?.message || "",
      )
    )
      return "连接超时或网络中断，请重试。若刚提交任务，请先查看任务记录，避免重复提交。";
    if (/unexpected.*json|not valid json/i.test(e?.message || ""))
      return "服务暂时未返回有效结果，请稍后重试。";
    return e?.message || "操作未完成，请稍后重试。";
  }
  root.CourseNestExperience = {
    semester,
    connection,
    task,
    error,
    activeStatuses,
    savedStatuses,
  };
})(globalThis);

<template>
  <div class="wrap">
    <header>
      <h1>企业办公数字员工 · Multi-Agent</h1>
      <p>自然语言任务 → 规划 → 执行 → 检索 → 审批 → 交付 → Trace</p>
    </header>

    <section class="row">
      <div class="panel grow">
        <label>任务（自然语言）</label>
        <textarea v-model="instruction" rows="3"
          placeholder="例：汇总本周5份部门周报，提取OKR，生成3页PPT，整理邮件，发送前让我确认"></textarea>
        <div class="btns">
          <input v-model.number="pages" placeholder="PPT页数" class="inline" />
          <button @click="create" :disabled="busy">提交任务</button>
          <button @click="approve(true)" :disabled="!pendingApproval">确认发送</button>
          <button @click="approve(false)" :disabled="!pendingApproval">拒绝</button>
          <button @click="poll" :disabled="!taskId">刷新</button>
        </div>
        <div class="hint">状态：{{ task?.status || '—' }}</div>
      </div>

      <div class="panel" v-if="pendingApproval">
        <h3>⚠️ 高风险操作待审批（{{ pendingApproval.tool }}）</h3>
        <pre class="kv">{{ JSON.stringify(pendingApproval.payload, null, 2) }}</pre>
        <div>风险等级：{{ pendingApproval.risk_level }}</div>
      </div>
    </section>

    <section class="row">
      <div class="panel grow">
        <h3>计划（{{ planSteps.length }} 步）</h3>
        <ol class="plan">
          <li v-for="s in planSteps" :key="s.step_id">
            <b>{{ s.action }}</b> → <code>{{ s.tool }}</code>
            <small v-if="s.depends_on.length"> 依赖 {{ s.depends_on.join(', ') }}</small>
          </li>
        </ol>
        <div v-if="clarification?.length">
          <b>需澄清：</b>{{ clarification.join('；') }}
        </div>
      </div>

      <div class="panel">
        <h3>Trace Timeline</h3>
        <div class="timeline">
          <div v-for="(e,i) in trace" :key="i" class="tl">
            <span class="dot" :class="e.status"></span>
            <span class="ag">{{ e.agent }}</span>
            <span class="ev">{{ e.event }}</span>
            <code v-if="e.tool">{{ e.tool }}</code>
            <span class="st">{{ e.status }}</span>
            <span v-if="e.latency_ms">· {{ e.latency_ms }}ms</span>
            <span v-if="e.tokens">· {{ e.tokens }}tok</span>
            <div class="err" v-if="e.error">{{ e.error }}</div>
          </div>
          <div v-if="!trace.length" class="empty">尚无事件</div>
        </div>
      </div>
    </section>

    <section class="row">
      <div class="panel grow">
        <h3>产物（Artifact Store）</h3>
        <table v-if="artifacts.length">
          <thead><tr><th>类型</th><th>摘要</th><th>路径</th></tr></thead>
          <tbody>
            <tr v-for="a in artifacts" :key="a.result_id">
              <td>{{ a.artifact_type }}</td>
              <td>{{ a.summary }}</td>
              <td class="mono">{{ a.storage_path }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty">暂无产物</div>
      </div>
    </section>
  </div>
</template>

<script>
import { ref, computed } from 'vue'
import axios from 'axios'

const API = import.meta.env.VITE_API_BASE || ''
export default {
  setup() {
    const instruction = ref('')
    const pages = ref(3)
    const busy = ref(false)
    const task = ref(null)
    const taskId = ref(null)

    async function create() {
      busy.value = true
      try {
        const r = await axios.post(API + '/tasks', {
          instruction: instruction.value, user_id: 'demo_user',
          deliverable: 'markdown'
        })
        taskId.value = r.data.task_id
        await poll()
      } finally { busy.value = false }
    }
    async function poll() {
      if (!taskId.value) return
      const [t, tr, a] = await Promise.all([
        axios.get(API + '/tasks/' + taskId.value),
        axios.get(API + '/tasks/' + taskId.value + '/trace'),
        axios.get(API + '/tasks/' + taskId.value + '/artifacts')
      ])
      task.value = t.data
      trace.value = tr.data.timeline || []
      artifacts.value = a.data.artifacts || []
      pendingApproval.value = t.data.pending_approval || null
    }
    async function approve(approved) {
      const r = await axios.post(API + '/tasks/' + taskId.value + '/approve',
        { approved })
      await poll()
    }

    const trace = ref([])
    const artifacts = ref([])
    const planSteps = computed(() => task.value?.plan_steps || [])
    const clarification = computed(() => task.value?.clarification_needed || [])
    const pendingApproval = computed(() => task.value?.pending_approval || null)

    return { instruction, pages, busy, task, taskId, create, poll, approve,
             trace, artifacts, planSteps, clarification, pendingApproval }
  }
}
</script>

<style>
body{font-family:system-ui,Segoe UI,sans-serif;margin:0;background:#0f1218;color:#e6e6e6}
.wrap{max-width:1200px;margin:0 auto;padding:24px}
header h1{margin:0;font-size:22px}
header p{color:#9aa;margin:6px 0 0;font-size:13px}
.row{display:flex;gap:16px;margin-top:16px;flex-wrap:wrap}
.panel{background:#171b24;border:1px solid #262c3a;border-radius:12px;padding:16px;flex:0 0 auto}
.grow{flex:1 1 380px}
textarea{width:100%;box-sizing:border-box;background:#0e1118;border:1px solid #2a3140;color:#e6e6e6;border-radius:8px;padding:8px;font-size:14px}
.btns{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap;align-items:center}
.btns button{background:#3b82f6;color:#fff;border:0;border-radius:8px;padding:8px 14px;cursor:pointer;font-size:13px}
.btns button:disabled{opacity:.4;cursor:not-allowed}
.inline{width:70px;background:#0e1118;border:1px solid #2a3140;color:#e6e6e6;border-radius:8px;padding:8px}
.hint{color:#9aa;font-size:12px;margin-top:8px}
h3{margin:0 0 10px;font-size:14px;color:#cfd6e4}
.plan{margin:0;padding-left:18px;line-height:1.8}
.plan code{background:#0e1118;padding:2px 6px;border-radius:4px;font-size:12px}
.timeline{max-height:320px;overflow:auto}
.tl{display:flex;align-items:center;gap:8px;padding:5px 0;font-size:13px;border-bottom:1px solid #1e2431}
.dot{width:9px;height:9px;border-radius:50%;background:#555;flex:0 0 9px}
.dot.OK{background:#22c55e}.dot.ERROR{background:#ef4444}.dot.HIT{background:#eab308}
.ag{width:90px;color:#8aa}.ev{width:110px;color:#cfd}.st{margin-left:auto;color:#888}
.err{width:100%;color:#f88;font-size:11px;margin-top:2px}
.empty{color:#667;font-size:13px;padding:8px 0}
.kv{background:#0e1118;border-radius:8px;padding:8px;font-size:12px;color:#fca}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px;border-bottom:1px solid #262c3a}
.mono{font-family:monospace;font-size:11px;color:#9aa}
</style>

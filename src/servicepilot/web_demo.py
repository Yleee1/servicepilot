from __future__ import annotations

import argparse
import json
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .baseline import RuleBasedToolCaller
from .llm_client import OpenAICompatibleJSONClient
from .mlx_client import MLXJSONClient
from .model_caller import ModelToolCaller
from .session import ProposalCaller, ServiceSession


PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ServicePilot</title><style>
:root{color-scheme:dark;--bg:#07111f;--panel:#0e1b2d;--line:#21334b;--text:#eef5ff;--muted:#90a4bd;--cyan:#4de3c1;--amber:#ffca68;--red:#ff7a90}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 ui-sans-serif,system-ui;background:radial-gradient(circle at 15% 0,#14365a 0,transparent 35%),var(--bg);color:var(--text)}
.wrap{max-width:1180px;margin:auto;padding:28px}.head{display:flex;justify-content:space-between;align-items:end;margin-bottom:18px}.brand{font-size:28px;font-weight:760}.brand span{color:var(--cyan)}
.sub,.muted{color:var(--muted)}.grid{display:grid;grid-template-columns:1.3fr .7fr;gap:18px}.card{background:color-mix(in srgb,var(--panel) 92%,transparent);border:1px solid var(--line);border-radius:16px;box-shadow:0 16px 45px #0005}
.chat{height:520px;overflow:auto;padding:20px}.bubble{max-width:86%;padding:12px 14px;margin:10px 0;border-radius:14px;white-space:pre-wrap}.user{margin-left:auto;background:#1d4e70}.agent{background:#14243a;border:1px solid var(--line)}
.status{display:inline-block;font-size:12px;padding:2px 8px;border-radius:20px;margin-bottom:5px;background:#203550;color:var(--cyan)}.confirmation_required{color:var(--amber)}.blocked{color:var(--red)}
.composer{border-top:1px solid var(--line);padding:14px;display:flex;gap:10px}input,select,button{border:1px solid var(--line);border-radius:10px;background:#0a1728;color:var(--text);padding:11px 12px}input{flex:1}button{cursor:pointer}button.primary{background:var(--cyan);color:#05201a;border:0;font-weight:700}
.side{padding:17px}.side h3{margin:6px 0 12px}.quick{display:flex;flex-wrap:wrap;gap:8px}.quick button{font-size:12px}.trace{font:12px/1.5 ui-monospace,SFMono-Regular;color:#bcd0e8;background:#071321;padding:12px;border-radius:10px;min-height:160px;white-space:pre-wrap;overflow:auto}.orders{display:grid;gap:7px}.order{padding:9px;border:1px solid var(--line);border-radius:9px;color:#cfe0f4}.foot{margin-top:10px;font-size:12px;color:var(--muted)}
@media(max-width:800px){.grid{grid-template-columns:1fr}.chat{height:420px}}
</style></head><body><div class="wrap">
<div class="head"><div><div class="brand">Service<span>Pilot</span></div><div class="sub">Executable customer-service agent · schema · policy · audit</div></div><div><select id="uid"><option>U001</option><option>U002</option><option>U003</option></select> <button onclick="resetSession()">重置会话</button></div></div>
<div class="grid"><section class="card"><div id="chat" class="chat"><div class="bubble agent"><span class="status">ready</span><br>可以查询订单、物流、库存，或通过两轮确认申请退款。</div></div>
<div class="composer"><input id="msg" placeholder="例如：O1002 商品坏了，帮我退款" autocomplete="off"><button class="primary" onclick="sendMessage()">发送</button></div></section>
<aside class="card side"><h3>快捷演示</h3><div class="quick"><button onclick="quick('查一下订单 O1001')">查询订单</button><button onclick="quick('O1001 的物流到哪了')">查询物流</button><button onclick="quick('O1002 商品坏了，帮我退款')">申请退款</button><button onclick="quick('确认')">确认</button><button onclick="quick('取消')">取消</button></div>
<h3>执行轨迹</h3><div id="trace" class="trace">等待请求…</div><h3>订单沙箱</h3><div id="orders" class="orders"></div><div class="foot">写操作必须经过参数来源校验、绑定确认、所有权和状态门禁。</div></aside></div></div>
<script>
const chat=document.getElementById('chat'),input=document.getElementById('msg'),trace=document.getElementById('trace'),orders=document.getElementById('orders');
function bubble(role,text,status){const d=document.createElement('div');d.className='bubble '+role; if(status){const s=document.createElement('span');s.className='status '+status;s.textContent=status;d.append(s,document.createElement('br'))}d.append(document.createTextNode(text));chat.append(d);chat.scrollTop=chat.scrollHeight}
function renderOrders(data){orders.replaceChildren();Object.values(data).forEach(o=>{const d=document.createElement('div');d.className='order';d.textContent=`${o.order_id} · ${o.status} · ${o.sku_id}`;orders.append(d)})}
async function sendMessage(){const text=input.value.trim();if(!text)return;input.value='';bubble('user',text);const r=await fetch('/api/message',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});const data=await r.json();bubble('agent',data.turn.message,data.turn.status);trace.textContent=JSON.stringify(data.turn,null,2);renderOrders(data.orders)}
function quick(t){input.value=t;sendMessage()} async function resetSession(){await fetch('/api/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:document.getElementById('uid').value})});chat.replaceChildren();bubble('agent','会话与订单沙箱已重置。','reset');trace.textContent='等待请求…';loadState()}
async function loadState(){const r=await fetch('/api/state');const d=await r.json();renderOrders(d.orders)} input.addEventListener('keydown',e=>{if(e.key==='Enter')sendMessage()});loadState();
</script></body></html>"""


class DemoState:
    def __init__(self, caller, user_id: str = "U001") -> None:
        self.caller = caller
        self.lock = threading.Lock()
        self.session = ServiceSession(user_id, ProposalCaller(caller))

    def reset(self, user_id: str) -> dict:
        with self.lock:
            self.session = ServiceSession(user_id, ProposalCaller(self.caller))
            return self.snapshot()

    def message(self, text: str) -> dict:
        with self.lock:
            turn = self.session.handle(text)
            return {"turn": asdict(turn), **self.snapshot()}

    def snapshot(self) -> dict:
        return {
            "user_id": self.session.user_id,
            "orders": {key: asdict(value) for key, value in self.session.registry.store.orders.items()},
        }


def make_handler(state: DemoState):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                self._send(PAGE.encode(), "text/html; charset=utf-8")
            elif self.path == "/api/state":
                self._json(state.snapshot())
            else:
                self.send_error(404)

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/api/message":
                    text = str(payload.get("text", "")).strip()
                    if not text:
                        return self._json({"error": "text is required"}, 400)
                    return self._json(state.message(text))
                if self.path == "/api/reset":
                    user_id = str(payload.get("user_id", "U001"))
                    if user_id not in {"U001", "U002", "U003"}:
                        return self._json({"error": "invalid user_id"}, 400)
                    return self._json(state.reset(user_id))
                self.send_error(404)
            except (ValueError, json.JSONDecodeError) as exc:
                self._json({"error": str(exc)}, 400)

        def _json(self, value: dict, status: int = 200):
            self._send(json.dumps(value, ensure_ascii=False).encode(), "application/json", status)

        def _send(self, body: bytes, content_type: str, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    return Handler


def build_caller(args):
    if args.caller == "deepseek":
        return ModelToolCaller(OpenAICompatibleJSONClient.from_env(args.env_file))
    if args.caller == "mlx":
        client = MLXJSONClient(args.model, args.adapter_path, args.adapter_weights)
        return ModelToolCaller(client)
    return RuleBasedToolCaller()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ServicePilot local web demo")
    parser.add_argument("--caller", choices=("rule", "deepseek", "mlx"), default="rule")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--model", default="models/Qwen3-1.7B-4bit")
    parser.add_argument("--adapter-path")
    parser.add_argument("--adapter-weights")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(DemoState(build_caller(args))))
    print(f"ServicePilot demo: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

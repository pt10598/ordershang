import base64, hashlib, hmac, json, os, re, secrets, threading, time as time_module, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import firebase_admin
from fastapi import BackgroundTasks, FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from firebase_admin import credentials, firestore
from google.cloud import firestore as google_firestore
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.middleware.sessions import SessionMiddleware

BASE_DIR = Path(__file__).resolve().parent
SESSION_SECRET=os.getenv("SESSION_SECRET",secrets.token_urlsafe(48))
app = FastAPI(title="膳雉坊線上訂餐")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, https_only=bool(os.getenv("DYNO")), same_site="lax")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
cancel_serializer=URLSafeTimedSerializer(SESSION_SECRET,salt="customer-order-cancel")
line_status_serializer=URLSafeTimedSerializer(SESSION_SECRET,salt="line-order-status")
TAIPEI_TZ=timezone(timedelta(hours=8))

def taipei_datetime(value):
 if isinstance(value,datetime):dt=value
 elif value:
  try:dt=datetime.fromisoformat(str(value).replace("Z","+00:00"))
  except ValueError:return None
 else:return None
 if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
 return dt.astimezone(TAIPEI_TZ)

def format_taipei_datetime(value):
 dt=taipei_datetime(value)
 return dt.strftime("%Y-%m-%d %H:%M") if dt else "—"

templates.env.filters["tw_datetime"]=format_taipei_datetime

def pickup_slot_is_future(date_value,slot_value,now=None):
 try:pickup_at=datetime.strptime(f"{date_value} {slot_value}","%Y-%m-%d %H:%M").replace(tzinfo=TAIPEI_TZ)
 except (TypeError,ValueError):return False
 return pickup_at>(now or datetime.now(TAIPEI_TZ))

DEFAULT_MEALS = []
DEFAULT_LOCATIONS = []
DEFAULT_STORES = [
 {"id":"store-shanzhifang","name":"膳雉坊","logo_url":"/static/shanzhifang-logo.jpg","active":True,"sort":1},
]
def make_time_slots(start_hour,start_minute,end_hour,end_minute):
 current=datetime(2000,1,1,start_hour,start_minute); end=datetime(2000,1,1,end_hour,end_minute); slots=[]
 while current<=end:
  slots.append(current.strftime("%H:%M")); current+=timedelta(minutes=10)
 return slots

MORNING_PICKUP_SLOTS=make_time_slots(11,0,14,0)
AFTERNOON_PICKUP_SLOTS=make_time_slots(16,30,19,30)
DEFAULT_PICKUP_SLOTS=MORNING_PICKUP_SLOTS+AFTERNOON_PICKUP_SLOTS

def fixed_pickup_slots(morning_open=True,afternoon_open=True):
 return (MORNING_PICKUP_SLOTS if morning_open else [])+(AFTERNOON_PICKUP_SLOTS if afternoon_open else [])

class MemoryStore:
 def __init__(self):
  self.meals={x["id"]:x.copy() for x in DEFAULT_MEALS}; self.locations={x["id"]:x.copy() for x in DEFAULT_LOCATIONS}; self.stores={x["id"]:x.copy() for x in DEFAULT_STORES}; self.orders={}
  self.settings={"order_date":datetime.now().strftime("%Y-%m-%d"),"headline":"膳雉坊・美味現點","ordering_open":True}
  self.pickup_dates={"date-default":{"id":"date-default","date":self.settings["order_date"],"pickup_slots":DEFAULT_PICKUP_SLOTS,"morning_open":True,"afternoon_open":True,"fixed_periods_v1":True,"active":True,"sort":1}}
  self.schedules={f"schedule-{i+1}":{"id":f"schedule-{i+1}","date":self.settings["order_date"],"location_id":x["id"],"location_name":x["name"],"pickup_slots":x["pickup_slots"],"active":True,"sort":x["sort"]} for i,x in enumerate(DEFAULT_LOCATIONS)}
memory=MemoryStore()
HOME_CACHE={"data":None,"expires_at":0.0}
HOME_CACHE_LOCK=threading.Lock()

def invalidate_home_cache():
 with HOME_CACHE_LOCK:
  HOME_CACHE["data"]=None; HOME_CACHE["expires_at"]=0.0

class NamespacedFirestore:
 def __init__(self,client,namespace):self.client=client; self.namespace=namespace
 def collection(self,name):return self.client.collection(f"{self.namespace}_{name}")
 def transaction(self):return self.client.transaction()

def init_firestore():
 encoded=os.getenv("FIREBASE_CREDENTIALS_BASE64")
 if not encoded:return None
 try:
  info=json.loads(base64.b64decode(encoded).decode())
  if not firebase_admin._apps:firebase_admin.initialize_app(credentials.Certificate(info))
  namespace=re.sub(r"[^a-zA-Z0-9_-]","",os.getenv("FIRESTORE_NAMESPACE","shanzhifang")) or "shanzhifang"
  return NamespacedFirestore(firestore.client(),namespace)
 except Exception as exc:
  print(f"Firestore initialization failed: {exc}"); return None
db=init_firestore()

def list_collection(name, active_only=False):
 if db:
  rows=[]
  for doc in db.collection(name).stream():
   item=doc.to_dict() or {}; item["id"]=doc.id
   if not active_only or item.get("active",True):rows.append(item)
 else:
  source={"meals":memory.meals,"locations":memory.locations,"stores":memory.stores,"pickup_dates":memory.pickup_dates}[name]
  rows=[x.copy() for x in source.values() if not active_only or x.get("active",True)]
 return sorted(rows,key=lambda x:(x.get("date","") if name=="pickup_dates" else "",x.get("sort",999),x.get("name","")))

def get_item(name,item_id):
 if db:
  doc=db.collection(name).document(item_id).get()
  return ({"id":doc.id,**(doc.to_dict() or {})} if doc.exists else None)
 return {"meals":memory.meals,"locations":memory.locations,"stores":memory.stores,"pickup_dates":memory.pickup_dates}[name].get(item_id)

def save_item(name,item_id,data):
 if db:db.collection(name).document(item_id).set(data,merge=True)
 else:{"meals":memory.meals,"locations":memory.locations,"stores":memory.stores,"pickup_dates":memory.pickup_dates}[name][item_id]={"id":item_id,**data}
 invalidate_home_cache()

def get_settings():
 if db:
  doc=db.collection("settings").document("ordering").get()
  if doc.exists:return doc.to_dict()
 return memory.settings.copy()

def save_settings(data):
 if db:db.collection("settings").document("ordering").set(data,merge=True)
 else:memory.settings.update(data)
 invalidate_home_cache()

def list_schedules(active_only=False):
 if db:
  rows=[]
  for doc in db.collection("schedules").stream():
   item=doc.to_dict() or {}; item["id"]=doc.id
   if not active_only or item.get("active",True):rows.append(item)
 else:rows=[x.copy() for x in memory.schedules.values() if not active_only or x.get("active",True)]
 return sorted(rows,key=lambda x:(x.get("date",""),x.get("sort",999),x.get("location_name","")))

def get_schedule(date,location_id):
 return next((x for x in list_schedules(True) if x.get("date")==date and x.get("location_id")==location_id),None)

def save_schedule(schedule_id,data):
 if db:db.collection("schedules").document(schedule_id).set(data,merge=True)
 else:memory.schedules[schedule_id]={"id":schedule_id,**data}
 invalidate_home_cache()

def get_home_data():
 now=time_module.monotonic()
 with HOME_CACHE_LOCK:
  if HOME_CACHE["data"] is not None and HOME_CACHE["expires_at"]>now:return HOME_CACHE["data"]
 with ThreadPoolExecutor(max_workers=5) as pool:
  jobs={
   "meals":pool.submit(list_collection,"meals",True),
   "stores":pool.submit(list_collection,"stores",True),
   "locations":pool.submit(list_collection,"locations",True),
   "pickup_dates":pool.submit(list_collection,"pickup_dates",True),
   "settings":pool.submit(get_settings),
  }
  data={name:job.result() for name,job in jobs.items()}
 ttl=max(5,min(int(os.getenv("HOME_CACHE_SECONDS","60")),300))
 with HOME_CACHE_LOCK:
  HOME_CACHE["data"]=data; HOME_CACHE["expires_at"]=time_module.monotonic()+ttl
 return data

def create_order(data):
 oid=datetime.now().strftime("%y%m%d")+"-"+secrets.token_hex(3).upper()
 if db:db.collection("orders").document(oid).set(data)
 else:memory.orders[oid]={"id":oid,**data}
 return oid

def create_order_once(data,checkout_token):
 """Create exactly one order for a browser checkout attempt."""
 oid=datetime.now().strftime("%y%m%d")+"-"+hashlib.sha256(checkout_token.encode()).hexdigest()[:8].upper()
 if db:
  ref=db.collection("orders").document(oid); transaction=db.transaction()
  @google_firestore.transactional
  def commit_once(txn):
   snapshot=ref.get(transaction=txn)
   if snapshot.exists:return False
   txn.set(ref,data); return True
  created=commit_once(transaction)
 else:
  created=oid not in memory.orders
  if created:memory.orders[oid]={"id":oid,**data}
 return oid,created

def update_order(oid,data):
 if db:db.collection("orders").document(oid).set(data,merge=True)
 elif oid in memory.orders:memory.orders[oid].update(data)

def get_order(oid):
 if db:
  doc=db.collection("orders").document(oid).get(); return ({"id":doc.id,**(doc.to_dict() or {})} if doc.exists else None)
 return memory.orders.get(oid)

def list_orders():
 rows=([{"id":d.id,**(d.to_dict() or {})} for d in db.collection("orders").stream()] if db else list(memory.orders.values()))
 return sorted(rows,key=lambda x:x.get("created_at",""),reverse=True)

def list_orders_by_phone(phone):
 if db:
  docs=db.collection("orders").where("phone","==",phone).stream()
  rows=[{"id":doc.id,**(doc.to_dict() or {})} for doc in docs]
 else:rows=[order.copy() for order in memory.orders.values() if order.get("phone")==phone]
 return sorted(rows,key=lambda x:x.get("created_at",""),reverse=True)

def customer_orders(phone):
 rows=list_orders_by_phone(phone)
 for order in rows:
  order["cancel_token"]=cancel_serializer.dumps({"order_id":order["id"],"phone":phone})
 return rows

def line_configured():
 return bool(os.getenv("LINE_CHANNEL_SECRET") and os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))

def line_pay_configured():
 return bool(os.getenv("LINE_PAY_CHANNEL_ID") and os.getenv("LINE_PAY_CHANNEL_SECRET"))

def line_pay_base_url():
 return "https://api-pay.line.me" if os.getenv("LINE_PAY_ENV","sandbox").lower()=="production" else "https://sandbox-api-pay.line.me"

def line_pay_request(api_path,payload):
 channel_id=os.getenv("LINE_PAY_CHANNEL_ID",""); channel_secret=os.getenv("LINE_PAY_CHANNEL_SECRET","")
 if not channel_id or not channel_secret:raise RuntimeError("LINE Pay 尚未設定")
 body=json.dumps(payload,ensure_ascii=False,separators=(",",":")); nonce=secrets.token_hex(16)
 signature=base64.b64encode(hmac.new(channel_secret.encode(),f"{channel_secret}{api_path}{body}{nonce}".encode(),hashlib.sha256).digest()).decode()
 req=urllib.request.Request(f"{line_pay_base_url()}{api_path}",data=body.encode(),headers={"Content-Type":"application/json","X-LINE-ChannelId":channel_id,"X-LINE-Authorization-Nonce":nonce,"X-LINE-Authorization":signature},method="POST")
 try:
  with urllib.request.urlopen(req,timeout=45) as response:return json.loads(response.read())
 except urllib.error.HTTPError as exc:
  detail=exc.read().decode(errors="replace"); raise RuntimeError(f"LINE Pay HTTP {exc.code}: {detail[:300]}") from exc
 except (urllib.error.URLError,TimeoutError,json.JSONDecodeError) as exc:raise RuntimeError(f"LINE Pay 連線失敗：{exc}") from exc

def ensure_line_pairing_code():
 settings=get_settings()
 if settings.get("line_group_id"):return ""
 code=settings.get("line_pairing_code")
 if not code:
  code=secrets.token_hex(3).upper(); save_settings({"line_pairing_code":code})
 return code

def line_api_request(path,payload=None,method="POST"):
 token=os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
 if not token:return None
 data=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None
 request=urllib.request.Request(f"https://api.line.me{path}",data=data,headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},method=method)
 try:
  with urllib.request.urlopen(request,timeout=10) as response:
   body=response.read()
   return json.loads(body) if body else {}
 except (urllib.error.URLError,urllib.error.HTTPError,TimeoutError,json.JSONDecodeError) as exc:
  print(f"LINE API failed: {exc}")
  return None

def push_line_messages(to,messages):
 if to:line_api_request("/v2/bot/message/push",{"to":to,"messages":messages})

def push_line_message(to,text):push_line_messages(to,[{"type":"text","text":text}])

def reply_line_message(reply_token,text):
 if reply_token:line_api_request("/v2/bot/message/reply",{"replyToken":reply_token,"messages":[{"type":"text","text":text}]})

def line_member_name(group_id,user_id):
 if not group_id or not user_id:return "LINE 群組成員"
 profile=line_api_request(f"/v2/bot/group/{urllib.parse.quote(group_id,safe='')}/member/{urllib.parse.quote(user_id,safe='')}",method="GET")
 return profile.get("displayName","LINE 群組成員") if isinstance(profile,dict) else "LINE 群組成員"

STATUS_LABELS={"new":"新訂單","confirmed":"已確認","completed":"已完成","picked_up":"已取餐","cancelled":"已取消"}

def send_order_notification(oid,order):
 group_id=get_settings().get("line_group_id")
 if not group_id:return
 item_lines="\n".join(f"・{item['name']} × {item['qty']}　NT$ {item['subtotal']}" for item in order["items"])
 note=f"\n備註：{order['note']}" if order.get("note") else ""
 message=(f"🍗 膳雉坊・新訂單通知\n"
          f"訂單編號：{oid}\n"
          f"訂購人：{order['customer_name']}\n"
          f"手機：{order['phone']}\n"
          f"取餐日期：{order['pickup_date']}\n"
          f"取餐地點：{order['location_name']}\n"
          f"取餐時間：{order['pickup_time']}\n"
          f"付款：{'LINE Pay 已付款' if order.get('payment_status')=='paid' else '現場付款'}\n"
          f"憑證：{order.get('invoice_label','收據')}\n\n"
          f"{item_lines}\n\n合計：NT$ {order['total']}{note}")[:4500]
 button_colors={"confirmed":"#2563EB","completed":"#0D9488","picked_up":"#0F766E","cancelled":"#DC2626"}
 buttons=[]
 for status in ("confirmed","completed","picked_up","cancelled"):
  token=line_status_serializer.dumps({"order_id":oid,"status":status,"group_id":group_id})
  buttons.append({"type":"button","style":"primary","height":"sm","margin":"sm","color":button_colors[status],"action":{"type":"postback","label":STATUS_LABELS[status],"data":f"order_status:{token}"}})
 flex={"type":"flex","altText":f"膳雉坊新訂單 {oid}｜{order['customer_name']}｜NT$ {order['total']}","contents":{"type":"bubble","size":"mega","body":{"type":"box","layout":"vertical","contents":[{"type":"text","text":message,"wrap":True,"size":"sm","color":"#211817"}]},"footer":{"type":"box","layout":"vertical","spacing":"sm","contents":buttons}}}
 push_line_messages(group_id,[flex])

def send_status_notification(oid,order,status):
 group_id=get_settings().get("line_group_id")
 if not group_id:return
 icons={"new":"🆕","confirmed":"✅","completed":"🎉","picked_up":"🥡","cancelled":"❌"}
 label=STATUS_LABELS.get(status,status)
 message=(f"{icons.get(status,'📌')} 膳雉坊・訂單狀態更新\n"
          f"訂單編號：{oid}\n"
          f"目前狀態：{label}\n"
          f"訂購人：{order.get('customer_name','')}\n"
          f"手機：{order.get('phone','')}\n"
          f"取餐日期：{order.get('pickup_date','')}\n"
          f"取餐地點：{order.get('location_name','')}\n"
          f"取餐時間：{order.get('pickup_time','')}")
 push_line_message(group_id,message)

def stable_store_id(name):return "store-"+hashlib.sha1(name.strip().encode()).hexdigest()[:10]

def ensure_stores():
 stores=list_collection("stores")
 by_name={item.get("name","").strip():item for item in stores}
 for meal in list_collection("meals"):
  store_name=meal.get("store","").strip()
  if not store_name:continue
  store=by_name.get(store_name)
  if not store:
   store_id=stable_store_id(store_name); save_item("stores",store_id,{"name":store_name,"active":True,"sort":len(by_name)+1}); store={"id":store_id,"name":store_name}; by_name[store_name]=store
  if meal.get("store_id")!=store["id"]:save_item("meals",meal["id"],{"store_id":store["id"]})

def slot_key(date,slot):return f"{date}|{slot}"

def ensure_availability_model():
 schedules=list_schedules(); pickup_dates=list_collection("pickup_dates")
 if not pickup_dates:
  dates={}
  for schedule in schedules:
   if not schedule.get("date"):continue
   dates.setdefault(schedule["date"],set()).update(schedule.get("pickup_slots") or [])
  if not dates:dates[get_settings().get("order_date") or datetime.now().strftime("%Y-%m-%d")]=set(DEFAULT_PICKUP_SLOTS)
  for index,(date,slots) in enumerate(sorted(dates.items())):
   save_item("pickup_dates",f"date-{hashlib.sha1(date.encode()).hexdigest()[:10]}",{"date":date,"pickup_slots":sorted(slots),"active":True,"sort":index+1})
 for location in list_collection("locations"):
  if location.get("availability_configured"):continue
  keys=[]
  for schedule in schedules:
   if schedule.get("location_id")==location["id"] and schedule.get("active",True):keys.extend(slot_key(schedule.get("date",""),slot) for slot in schedule.get("pickup_slots") or [])
  if not keys:
   for date_config in list_collection("pickup_dates",True):keys.extend(slot_key(date_config.get("date",""),slot) for slot in date_config.get("pickup_slots") or [])
  save_item("locations",location["id"],{"slot_keys":sorted(set(keys)),"availability_configured":True})
 all_location_ids=[item["id"] for item in list_collection("locations",True)]
 for store in list_collection("stores"):
  if store.get("locations_configured"):continue
  linked=set()
  for schedule in schedules:
   if not schedule.get("stores_configured") or store["id"] in (schedule.get("store_ids") or []):linked.add(schedule.get("location_id"))
  save_item("stores",store["id"],{"location_ids":sorted(item for item in linked if item) or all_location_ids,"locations_configured":True})

def ensure_fixed_pickup_periods():
 """Migrate older manually entered times to the fixed morning/afternoon periods once."""
 migrations={}
 for config in list_collection("pickup_dates"):
  if config.get("fixed_periods_v1"):continue
  old_slots=set(config.get("pickup_slots") or [])
  morning_open=any(slot in old_slots for slot in MORNING_PICKUP_SLOTS) if old_slots else True
  afternoon_open=any(slot in old_slots for slot in AFTERNOON_PICKUP_SLOTS) if old_slots else True
  new_slots=fixed_pickup_slots(morning_open,afternoon_open)
  migrations[config.get("date","")]={"old":old_slots,"new":set(new_slots),"morning":morning_open,"afternoon":afternoon_open}
  save_item("pickup_dates",config["id"],{"pickup_slots":new_slots,"morning_open":morning_open,"afternoon_open":afternoon_open,"fixed_periods_v1":True,"active":config.get("active",True) and bool(new_slots)})
 if not migrations:return
 for location in list_collection("locations"):
  keys=set(location.get("slot_keys") or []); changed=False
  for date,migration in migrations.items():
   date_prefix=f"{date}|"; existing={key for key in keys if key.startswith(date_prefix)}
   if not existing:continue
   keys-=existing
   selected_slots={key.split("|",1)[1] for key in existing}
   use_morning=any(slot in MORNING_PICKUP_SLOTS for slot in selected_slots)
   use_afternoon=any(slot in AFTERNOON_PICKUP_SLOTS for slot in selected_slots)
   if use_morning and migration["morning"]:keys.update(slot_key(date,slot) for slot in MORNING_PICKUP_SLOTS)
   if use_afternoon and migration["afternoon"]:keys.update(slot_key(date,slot) for slot in AFTERNOON_PICKUP_SLOTS)
   changed=True
  if changed:save_item("locations",location["id"],{"slot_keys":sorted(keys)})

def seed_database():
 if not db:
  ensure_stores(); ensure_availability_model(); return
 if not next(db.collection("meals").limit(1).stream(),None):
  for x in DEFAULT_MEALS:save_item("meals",x["id"],{k:v for k,v in x.items() if k!="id"})
 if not next(db.collection("locations").limit(1).stream(),None):
  for x in DEFAULT_LOCATIONS:save_item("locations",x["id"],{k:v for k,v in x.items() if k!="id"})
 if not next(db.collection("stores").limit(1).stream(),None):
  for x in DEFAULT_STORES:save_item("stores",x["id"],{k:v for k,v in x.items() if k!="id"})
 if not db.collection("settings").document("ordering").get().exists:save_settings(memory.settings)
 if not next(db.collection("schedules").limit(1).stream(),None):
  settings=get_settings(); default_date=settings.get("order_date") or datetime.now().strftime("%Y-%m-%d")
  for i,location in enumerate(list_collection("locations",True)):
   save_schedule(f"schedule-{i+1}",{"date":default_date,"location_id":location["id"],"location_name":location["name"],"pickup_slots":pickup_slots_for(location),"active":True,"sort":location.get("sort",i+1)})
 settings=get_settings()
 if "膳雞坊" in settings.get("headline",""):save_settings({"headline":settings["headline"].replace("膳雞坊","膳雉坊")})
 for store in list_collection("stores"):
  if store.get("name")=="膳雞坊":save_item("stores",store["id"],{"name":"膳雉坊"})
 for meal in list_collection("meals"):
  if meal.get("store")=="膳雞坊":save_item("meals",meal["id"],{"store":"膳雉坊"})
 ensure_stores()
 ensure_availability_model()
 ensure_fixed_pickup_periods()

def is_admin(request):return request.session.get("admin") is True
def render(request,name,**context):return templates.TemplateResponse(request=request,name=name,context=context)
def pickup_slots_for(location):
 slots=location.get("pickup_slots") if location else None
 return slots if isinstance(slots,list) and slots else DEFAULT_PICKUP_SLOTS

@app.on_event("startup")
async def startup():seed_database()

@app.get("/",response_class=HTMLResponse)
async def home(request:Request):
 cached=get_home_data(); now=datetime.now(TAIPEI_TZ); data={**cached}
 data["pickup_dates"]=[]
 for config in cached["pickup_dates"]:
  available_slots=[slot for slot in config.get("pickup_slots") or [] if pickup_slot_is_future(config.get("date"),slot,now)]
  if available_slots:data["pickup_dates"].append({**config,"pickup_slots":available_slots})
 return render(request,"index.html",**data,line_pay_configured=line_pay_configured(),line_pay_sandbox=os.getenv("LINE_PAY_ENV","sandbox").lower()!="production")

@app.get("/order-lookup",response_class=HTMLResponse)
async def order_lookup_page(request:Request):
 return render(request,"order_lookup.html",orders=[],phone="",searched=False,error=None,success=None)

@app.post("/order-lookup",response_class=HTMLResponse)
async def order_lookup(request:Request,phone:str=Form(...)):
 phone=re.sub(r"\D","",phone)
 if not re.fullmatch(r"09\d{8}",phone):
  return render(request,"order_lookup.html",orders=[],phone=phone,searched=False,error="請輸入正確的 10 碼手機號碼，例如 0912345678。",success=None)
 return render(request,"order_lookup.html",orders=customer_orders(phone),phone=phone,searched=True,error=None,success=None)

@app.post("/orders/{oid}/cancel",response_class=HTMLResponse)
async def customer_cancel_order(request:Request,background_tasks:BackgroundTasks,oid:str,phone:str=Form(...),cancel_token:str=Form(...)):
 phone=re.sub(r"\D","",phone)
 try:token_data=cancel_serializer.loads(cancel_token,max_age=1800)
 except SignatureExpired:return render(request,"message.html",title="取消連結已逾時",message="請回到訂單查詢頁重新查詢後再取消。")
 except BadSignature:return render(request,"message.html",title="無法取消訂單",message="取消驗證資料不正確，請重新查詢訂單。")
 if token_data.get("order_id")!=oid or token_data.get("phone")!=phone:return render(request,"message.html",title="無法取消訂單",message="訂單資料不一致，請重新查詢。")
 order=get_order(oid)
 if not order or order.get("phone")!=phone:return render(request,"message.html",title="找不到訂單",message="請確認手機號碼與訂單資料。")
 if order.get("status") in {"picked_up","cancelled"}:return render(request,"order_lookup.html",orders=customer_orders(phone),phone=phone,searched=True,error="此訂單已取餐或已取消，無法再次取消。",success=None)
 if order.get("payment_status") in {"paid","verification_pending"}:return render(request,"order_lookup.html",orders=customer_orders(phone),phone=phone,searched=True,error="此訂單已付款或付款結果仍在確認中；退款功能尚未串接，請聯繫店家處理，避免只取消訂單但未退款。",success=None)
 now=datetime.now(timezone.utc).isoformat()
 if db:db.collection("orders").document(oid).set({"status":"cancelled","cancelled_by":"customer","cancelled_at":now,"updated_at":now},merge=True)
 elif oid in memory.orders:memory.orders[oid].update({"status":"cancelled","cancelled_by":"customer","cancelled_at":now,"updated_at":now})
 background_tasks.add_task(send_status_notification,oid,order,"cancelled")
 return render(request,"order_lookup.html",orders=customer_orders(phone),phone=phone,searched=True,error=None,success=f"訂單 {oid} 已成功取消，管理員將收到通知。")

@app.post("/orders")
async def submit_order(request:Request,background_tasks:BackgroundTasks,customer_name:str=Form(...),phone:str=Form(...),location_id:str=Form(...),pickup_date:str=Form(...),pickup_time:str=Form(...),invoice_type:str=Form("receipt"),payment_method:str=Form("onsite"),checkout_token:str=Form(""),mobile_barcode:str=Form(""),tax_id:str=Form(""),note:str=Form(""),items_json:str=Form(...)):
 if not get_settings().get("ordering_open",True):return render(request,"message.html",title="目前已截止訂餐",message="請等待下一次菜單開放。")
 phone=re.sub(r"\D","",phone)
 if not re.fullmatch(r"09\d{8}",phone):return render(request,"message.html",title="手機號碼格式錯誤",message="請輸入正確的 10 碼手機號碼。")
 if invoice_type!="receipt":return render(request,"message.html",title="憑證方式錯誤",message="膳雉坊目前僅提供收據。")
 if payment_method not in {"onsite","line_pay"}:return render(request,"message.html",title="付款方式錯誤",message="請重新選擇付款方式。")
 if payment_method=="line_pay" and not line_pay_configured():return render(request,"message.html",title="LINE Pay 尚未開放",message="測試金鑰尚未設定，請先使用現場付款。")
 checkout_token=checkout_token.strip()
 if not re.fullmatch(r"[0-9a-fA-F-]{32,64}",checkout_token):checkout_token=secrets.token_hex(24)
 mobile_barcode=""; tax_id=""
 try:requested=json.loads(items_json)
 except json.JSONDecodeError:requested=[]
 date_config=next((item for item in list_collection("pickup_dates",True) if item.get("date")==pickup_date),None)
 location=get_item("locations",location_id)
 selected_slot_key=slot_key(pickup_date,pickup_time)
 if not date_config or pickup_time not in (date_config.get("pickup_slots") or []):return render(request,"message.html",title="取餐時間無效",message="請重新選擇開放中的取餐日期與時間。")
 if not pickup_slot_is_future(pickup_date,pickup_time):return render(request,"message.html",title="取餐時間已截止",message="這個取餐時間已經超過，請返回菜單重新選擇其他時間。")
 if not location or not location.get("active",True) or (location.get("availability_configured") and selected_slot_key not in (location.get("slot_keys") or [])):return render(request,"message.html",title="訂單沒有送出",message="這個取餐地點目前未開放所選時段，請重新選擇。")
 items=[]; total=0
 for row in requested:
  meal=get_item("meals",str(row.get("id",""))); qty=max(0,min(int(row.get("qty",0)),99))
  if not meal or not meal.get("active",True) or not qty:continue
  store=get_item("stores",meal.get("store_id",""))
  if not store or not store.get("active",True):continue
  if store.get("locations_configured") and location_id not in (store.get("location_ids") or []):continue
  options=meal.get("options") or []; selected_options=[]
  if options:
   requested_names=row.get("option_names") if isinstance(row.get("option_names"),list) else [row.get("option_name","")]
   requested_names=[str(value).strip() for value in requested_names if str(value).strip()]
   requested_names=list(dict.fromkeys(requested_names))
   option_map={str(item.get("name","")):item for item in options}
   selected_options=[option_map[name] for name in requested_names if name in option_map]
   required=max(1,min(int(meal.get("option_select_count",1)),len(options))) if meal.get("option_multiple") else 1
   if len(selected_options)!=required or len(selected_options)!=len(requested_names):continue
  option_name="＋".join(str(item.get("name","")) for item in selected_options)
  price=sum(int(item.get("price",0)) for item in selected_options) if selected_options else int(meal.get("price",0)); display_name=f"{meal['name']}（{option_name}）" if option_name else meal["name"]
  items.append({"meal_id":meal["id"],"name":display_name,"base_name":meal["name"],"store_id":meal.get("store_id",""),"store":meal.get("store",""),"store_sort":int(store.get("sort",999)),"meal_sort":int(meal.get("sort",999)),"option_name":option_name,"option_names":[item.get("name","") for item in selected_options],"price":price,"qty":qty,"subtotal":price*qty}); total+=price*qty
 if not items:return render(request,"message.html",title="訂單沒有送出",message="選擇的餐點在此日期或地點未供應，請重新選擇。")
 items.sort(key=lambda item:(item.get("store_sort",999),item.get("meal_sort",999),item.get("name","")))
 now=datetime.now(timezone.utc).isoformat(); invoice_label="收據"; order={"customer_name":customer_name.strip(),"phone":phone.strip(),"location_id":location_id,"location_name":location["name"],"pickup_time":pickup_time,"pickup_date":pickup_date,"invoice_type":invoice_type,"mobile_barcode":"","tax_id":"","invoice_label":invoice_label,"invoice_status":"receipt","payment_method":payment_method,"payment_status":"pending" if payment_method=="line_pay" else "pay_on_pickup","checkout_token_hash":hashlib.sha256(checkout_token.encode()).hexdigest(),"note":note.strip(),"items":items,"total":total,"status":"new","created_at":now,"updated_at":now}; oid,created=create_order_once(order,checkout_token)
 if not created:
  existing=get_order(oid) or {}
  if existing.get("payment_status")=="paid" or existing.get("payment_method")!="line_pay":return RedirectResponse(f"/orders/{oid}/success",status_code=303)
  if existing.get("payment_status")=="pending" and existing.get("line_pay_payment_url"):return RedirectResponse(existing["line_pay_payment_url"],status_code=303)
  if existing.get("payment_status")=="verification_pending":return render(request,"message.html",title="付款結果確認中",message=f"訂單 {oid} 正在確認付款結果，請勿重新付款，稍後可由訂單查詢查看狀態。")
  return render(request,"message.html",title="這次付款未完成",message=f"訂單 {oid} 已取消或付款未成功；如要重新訂購，請返回菜單調整購物車後再送出。")
 if payment_method=="line_pay":
  base_url=str(request.base_url).rstrip("/")
  payload={"amount":total,"currency":"TWD","orderId":oid,"packages":[{"id":oid,"amount":total,"name":"膳雉坊線上訂餐","products":[{"id":item["meal_id"],"name":item["name"][:100],"quantity":item["qty"],"price":item["price"]} for item in items]}],"redirectUrls":{"confirmUrl":f"{base_url}/linepay/confirm?order_id={urllib.parse.quote(oid)}","cancelUrl":f"{base_url}/linepay/cancel?order_id={urllib.parse.quote(oid)}"}}
  try:result=line_pay_request("/v3/payments/request",payload)
  except RuntimeError as exc:
   update_order(oid,{"payment_status":"request_failed","status":"cancelled","payment_error":str(exc),"updated_at":datetime.now(timezone.utc).isoformat()}); return render(request,"message.html",title="LINE Pay 付款建立失敗",message=f"訂單編號 {oid} 未付款且已自動取消，請返回菜單重新下單。")
  if result.get("returnCode")!="0000" or not result.get("info",{}).get("paymentUrl"):
   update_order(oid,{"payment_status":"request_failed","status":"cancelled","payment_error":f"{result.get('returnCode','')} {result.get('returnMessage','')}","updated_at":datetime.now(timezone.utc).isoformat()}); return render(request,"message.html",title="LINE Pay 付款建立失敗",message=f"訂單編號 {oid} 未付款且已自動取消，錯誤：{result.get('returnMessage','未知錯誤')}")
  transaction_id=str(result["info"].get("transactionId",""))
  payment_url=result["info"]["paymentUrl"].get("web") or result["info"]["paymentUrl"].get("app")
  update_order(oid,{"line_pay_transaction_id":transaction_id,"line_pay_payment_url":payment_url,"updated_at":datetime.now(timezone.utc).isoformat()})
  return RedirectResponse(payment_url,status_code=303)
 background_tasks.add_task(send_order_notification,oid,order)
 return RedirectResponse(f"/orders/{oid}/success",status_code=303)

@app.get("/linepay/confirm",response_class=HTMLResponse)
async def line_pay_confirm(request:Request,background_tasks:BackgroundTasks,order_id:str,transactionId:str=""):
 order=get_order(order_id)
 if not order:return render(request,"message.html",title="找不到訂單",message="LINE Pay 回傳的訂單不存在。")
 if order.get("payment_status")=="paid":return RedirectResponse(f"/orders/{order_id}/success",status_code=303)
 expected_transaction=str(order.get("line_pay_transaction_id",transactionId))
 if not transactionId or transactionId!=expected_transaction:return render(request,"message.html",title="付款驗證失敗",message="LINE Pay 交易編號不一致，訂單尚未標記為付款成功。")
 try:result=line_pay_request(f"/v3/payments/{urllib.parse.quote(transactionId,safe='')}/confirm",{"amount":int(order.get("total",0)),"currency":"TWD"})
 except RuntimeError as exc:
  update_order(order_id,{"payment_status":"verification_pending","payment_error":str(exc),"updated_at":datetime.now(timezone.utc).isoformat()}); return render(request,"message.html",title="付款結果確認中",message=f"訂單 {order_id} 的付款結果尚待確認，請勿重複付款；店家可依交易編號查詢。")
 if result.get("returnCode")!="0000":
  update_order(order_id,{"payment_status":"failed","status":"cancelled","payment_error":f"{result.get('returnCode','')} {result.get('returnMessage','')}","updated_at":datetime.now(timezone.utc).isoformat()}); return render(request,"message.html",title="付款未成功",message=f"訂單 {order_id} 未付款且已自動取消：{result.get('returnMessage','請重新下單')}。")
 now=datetime.now(timezone.utc).isoformat(); updates={"payment_status":"paid","paid_at":now,"invoice_status":"pending","updated_at":now,"line_pay_confirm_result_code":result.get("returnCode")}; update_order(order_id,updates); order.update(updates)
 background_tasks.add_task(send_order_notification,order_id,order)
 return RedirectResponse(f"/orders/{order_id}/success",status_code=303)

@app.get("/linepay/cancel",response_class=HTMLResponse)
async def line_pay_cancel(request:Request,order_id:str):
 order=get_order(order_id)
 if order and order.get("payment_status")!="paid":update_order(order_id,{"payment_status":"cancelled","status":"cancelled","cancelled_by":"line_pay","updated_at":datetime.now(timezone.utc).isoformat()})
 return render(request,"message.html",title="LINE Pay 付款已取消",message=f"訂單 {order_id} 尚未付款，歡迎返回菜單重新下單。")

@app.post("/line/webhook")
async def line_webhook(request:Request):
 secret=os.getenv("LINE_CHANNEL_SECRET")
 if not secret:return HTMLResponse("LINE is not configured",status_code=503)
 body=await request.body(); signature=request.headers.get("x-line-signature","")
 expected=base64.b64encode(hmac.new(secret.encode(),body,hashlib.sha256).digest()).decode()
 if not hmac.compare_digest(signature,expected):return HTMLResponse("Invalid signature",status_code=400)
 try:payload=json.loads(body)
 except json.JSONDecodeError:return HTMLResponse("Invalid JSON",status_code=400)
 for event in payload.get("events",[]):
  source=event.get("source",{})
  if source.get("type")=="group" and source.get("groupId"):
   group_id=source["groupId"]
   settings=get_settings()
   if event.get("type")=="postback" and event.get("postback",{}).get("data","").startswith("order_status:"):
    reply_token=event.get("replyToken","")
    try:action=line_status_serializer.loads(event["postback"]["data"].split(":",1)[1],max_age=60*60*24*30)
    except (BadSignature,SignatureExpired):
     reply_line_message(reply_token,"⚠️ 此訂單操作按鈕已失效，請至管理後台確認。")
     continue
    oid=str(action.get("order_id","")); target_status=str(action.get("status",""))
    if action.get("group_id")!=group_id or settings.get("line_group_id")!=group_id or target_status not in {"confirmed","completed","picked_up","cancelled"}:
     reply_line_message(reply_token,"⚠️ 無法驗證這次操作，訂單狀態未變更。")
     continue
    order=get_order(oid)
    if not order:
     reply_line_message(reply_token,f"⚠️ 找不到訂單 {oid}，狀態未變更。")
     continue
    previous_status=order.get("status","new")
    if previous_status in {"picked_up","cancelled"}:
     reply_line_message(reply_token,f"ℹ️ 訂單 {oid} 已是「{STATUS_LABELS.get(previous_status,previous_status)}」，不能再由 LINE 變更。")
     continue
    if previous_status==target_status:
     reply_line_message(reply_token,f"ℹ️ 訂單 {oid} 已經是「{STATUS_LABELS[target_status]}」。")
     continue
    operator_id=source.get("userId",""); operator_name=line_member_name(group_id,operator_id)
    now=datetime.now(timezone.utc).isoformat(); history=list(order.get("status_history") or [])
    history.append({"from":previous_status,"to":target_status,"updated_at":now,"updated_by":operator_name,"updated_by_line_user_id":operator_id,"source":"line"})
    updates={"status":target_status,"updated_at":now,"updated_by":operator_name,"updated_by_line_user_id":operator_id,"updated_via":"line","status_history":history[-100:]}
    if db:db.collection("orders").document(oid).set(updates,merge=True)
    elif oid in memory.orders:memory.orders[oid].update(updates)
    reply_line_message(reply_token,(f"✅ 訂單狀態已更新\n"
                                    f"訂單編號：{oid}\n"
                                    f"狀態：{STATUS_LABELS.get(previous_status,previous_status)} → {STATUS_LABELS[target_status]}\n"
                                    f"操作者：{operator_name}\n"
                                    f"更新時間：{format_taipei_datetime(now)}"))
   elif event.get("type")=="leave" and settings.get("line_group_id")==group_id:save_settings({"line_group_id":"","line_pairing_code":""})
   elif event.get("type")=="message" and event.get("message",{}).get("type")=="text":
    pairing_code=settings.get("line_pairing_code","")
    expected_text=f"啟用訂單通知 {pairing_code}" if pairing_code else ""
    if not settings.get("line_group_id") and expected_text and event["message"].get("text","").strip()==expected_text:
     save_settings({"line_group_id":group_id,"line_group_connected_at":datetime.now(timezone.utc).isoformat(),"line_pairing_code":""})
     push_line_message(group_id,"✅ 膳雉坊訂單通知已連接\n之後有新訂單時，系統會自動通知此群組。")
 return {"ok":True}

@app.get("/orders/{oid}/success",response_class=HTMLResponse)
async def order_success(request:Request,oid:str):
 order=get_order(oid)
 return render(request,"order_success.html",order=order) if order else HTMLResponse("找不到訂單",status_code=404)

@app.get("/admin/login",response_class=HTMLResponse)
async def admin_login_page(request:Request):return render(request,"admin_login.html",error=None)

@app.post("/admin/login")
async def admin_login(request:Request,username:str=Form(...),password:str=Form(...)):
 expected_user=os.getenv("ADMIN_USERNAME","shanzhifang-admin"); expected_password=os.getenv("ADMIN_PASSWORD")
 if expected_password and secrets.compare_digest(username,expected_user) and secrets.compare_digest(password,expected_password):
  request.session.clear(); request.session["admin"]=True; return RedirectResponse("/admin",status_code=303)
 return render(request,"admin_login.html",error="帳號或密碼錯誤；若尚未設定 ADMIN_PASSWORD，請先到 Heroku Config Vars 設定。")

@app.post("/admin/logout")
async def admin_logout(request:Request):request.session.clear(); return RedirectResponse("/admin/login",status_code=303)

@app.get("/admin",response_class=HTMLResponse)
async def admin_dashboard(request:Request,view:str="cards",start_date:str="",start_time:str="",end_date:str="",end_time:str="",status:str="",search:str="",sort:str="newest"):
 if not is_admin(request):return RedirectResponse("/admin/login",status_code=303)
 all_orders=list_orders(); orders=[]
 meals_by_id={item["id"]:item for item in list_collection("meals")}
 stores=list_collection("stores"); stores_by_id={item["id"]:item for item in stores}; stores_by_name={item.get("name",""):item for item in stores}
 for order in all_orders:
  enriched=[]
  for original_index,row in enumerate(order.get("items") or []):
   item=dict(row); meal=meals_by_id.get(item.get("meal_id",""),{}); store=stores_by_id.get(item.get("store_id",'')) or stores_by_name.get(item.get("store",''),{})
   item["display_store"]=item.get("store") or store.get("name") or meal.get("store") or "未分類餐廳"
   item["display_store_sort"]=int(store.get("sort",999)); item["display_meal_sort"]=int(meal.get("sort",999)); item["original_index"]=original_index
   enriched.append(item)
  enriched.sort(key=lambda item:(item["display_store_sort"],item["display_meal_sort"],item["original_index"]))
  order["items"]=enriched
 try:start_at=datetime.combine(datetime.strptime(start_date,"%Y-%m-%d").date(),datetime.strptime(start_time or "00:00","%H:%M").time()) if start_date else None
 except ValueError:start_at=None
 try:end_at=datetime.combine(datetime.strptime(end_date,"%Y-%m-%d").date(),datetime.strptime(end_time or "23:59","%H:%M").time()) if end_date else None
 except ValueError:end_at=None
 query=search.strip().lower()
 for order in all_orders:
  created=taipei_datetime(order.get("created_at")); local_created=created.replace(tzinfo=None) if created else None
  if start_at and (not local_created or local_created<start_at):continue
  if end_at and (not local_created or local_created>end_at):continue
  if status and order.get("status")!=status:continue
  haystack=" ".join(str(order.get(key,"")) for key in ("id","customer_name","phone","location_name")).lower()
  if query and query not in haystack:continue
  orders.append(order)
 if sort=="oldest":orders.sort(key=lambda x:x.get("created_at",datetime.min.isoformat()))
 elif sort=="pickup":orders.sort(key=lambda x:(x.get("pickup_date",""),x.get("pickup_time","")))
 elif sort=="total_desc":orders.sort(key=lambda x:int(x.get("total",0)),reverse=True)
 else:orders.sort(key=lambda x:x.get("created_at",""),reverse=True)
 item_counts={}
 for order in orders:
  for item in order.get("items",[]):item_counts[item.get("name","未命名餐點")]=item_counts.get(item.get("name","未命名餐點"),0)+int(item.get("qty",0))
 summary={"orders":len(orders),"items":sum(item_counts.values()),"revenue":sum(int(x.get("total",0)) for x in orders),"item_counts":sorted(item_counts.items(),key=lambda x:(-x[1],x[0]))}
 filters={"view":view if view in {"cards","table"} else "cards","start_date":start_date,"start_time":start_time,"end_date":end_date,"end_time":end_time,"status":status,"search":search,"sort":sort}
 current_admin_url="/admin"+(f"?{request.url.query}" if request.url.query else "")
 return render(request,"admin_dashboard.html",orders=orders,new_count=sum(x.get("status")=="new" for x in all_orders),database_connected=db is not None,summary=summary,filters=filters,current_admin_url=current_admin_url)

@app.post("/admin/orders/{oid}/status")
async def order_status(request:Request,background_tasks:BackgroundTasks,oid:str,status:str=Form(...),return_to:str=Form("/admin")):
 if not is_admin(request):return RedirectResponse("/admin/login",status_code=303)
 if status not in {"new","confirmed","completed","picked_up","cancelled"}:status="new"
 order=get_order(oid)
 if not order:return RedirectResponse("/admin",status_code=303)
 previous_status=order.get("status","new")
 now=datetime.now(timezone.utc).isoformat(); history=list(order.get("status_history") or [])
 if status!=previous_status:history.append({"from":previous_status,"to":status,"updated_at":now,"updated_by":"管理後台","source":"admin"})
 updates={"status":status,"updated_at":now,"updated_by":"管理後台","updated_via":"admin","status_history":history[-100:]}
 if db:db.collection("orders").document(oid).set(updates,merge=True)
 elif oid in memory.orders:memory.orders[oid].update(updates)
 if status!=previous_status and status=="cancelled":background_tasks.add_task(send_status_notification,oid,order,status)
 safe_return=return_to if return_to.startswith("/admin") and not return_to.startswith("//") else "/admin"
 return RedirectResponse(safe_return,status_code=303)

@app.get("/admin/menu",response_class=HTMLResponse)
async def admin_menu(request:Request,edit:str|None=None):
 if not is_admin(request):return RedirectResponse("/admin/login",status_code=303)
 return render(request,"admin_menu.html",meals=list_collection("meals"),editing=get_item("meals",edit) if edit else None,locations=list_collection("locations"),stores=list_collection("stores"))

async def upload_image(file):
 if not file or not file.filename:return None
 raw=await file.read()
 if not raw:return None
 if os.getenv("CLOUDINARY_URL"):
  import cloudinary,cloudinary.uploader
  cloudinary.config(cloudinary_url=os.getenv("CLOUDINARY_URL"),secure=True)
  result=cloudinary.uploader.upload(raw,folder="shanzhifang-menu",resource_type="image"); return result.get("secure_url")
 from io import BytesIO
 from PIL import Image,ImageOps
 try:
  image=Image.open(BytesIO(raw)); image=ImageOps.exif_transpose(image); image.thumbnail((1200,1200))
  if image.mode not in ("RGB","L"):image=image.convert("RGB")
  output=BytesIO(); image.save(output,format="JPEG",quality=78,optimize=True)
  if output.tell()>600000:
   image.thumbnail((850,850)); output=BytesIO(); image.save(output,format="JPEG",quality=65,optimize=True)
  if output.tell()>650000:return None
  return "data:image/jpeg;base64,"+base64.b64encode(output.getvalue()).decode()
 except Exception as exc:
  print(f"Image processing failed: {exc}"); return None

@app.post("/admin/menu/save")
async def menu_save(request:Request,meal_id:str=Form(""),name:str=Form(...),store_id:str=Form(...),category:str=Form(...),description:str=Form(""),price:int=Form(...),image_url:str=Form(""),image_file:UploadFile|None=None,active:str|None=Form(None),sort:int=Form(99),location_ids:list[str]=Form([]),option_multiple:str|None=Form(None),option_select_count:int=Form(2),option_1_name:str=Form(""),option_1_price:int=Form(0),option_2_name:str=Form(""),option_2_price:int=Form(0),option_3_name:str=Form(""),option_3_price:int=Form(0),option_4_name:str=Form(""),option_4_price:int=Form(0)):
 if not is_admin(request):return RedirectResponse("/admin/login",status_code=303)
 store=get_item("stores",store_id)
 if not store:return RedirectResponse("/admin/menu",status_code=303)
 meal_id=meal_id or f"meal-{secrets.token_hex(4)}"; existing=get_item("meals",meal_id) or {}; uploaded=await upload_image(image_file)
 options=[]
 for option_name,option_price in ((option_1_name,option_1_price),(option_2_name,option_2_price),(option_3_name,option_3_price),(option_4_name,option_4_price)):
  if option_name.strip():options.append({"name":option_name.strip(),"price":max(option_price,0)})
 valid_location_ids={item["id"] for item in list_collection("locations")}; selected_locations=[item for item in location_ids if item in valid_location_ids]
 multiple=option_multiple=="on" and len(options)>1
 select_count=max(1,min(option_select_count,len(options))) if multiple else 1
 save_item("meals",meal_id,{"name":name.strip(),"store_id":store_id,"store":store["name"],"category":category.strip(),"description":description.strip(),"price":max(price,0),"image_url":uploaded or image_url.strip() or existing.get("image_url",""),"location_ids":selected_locations,"locations_configured":True,"options":options,"option_multiple":multiple,"option_select_count":select_count,"active":active=="on","sort":sort})
 return RedirectResponse("/admin/menu",status_code=303)

@app.post("/admin/menu/{meal_id}/toggle")
async def menu_toggle(request:Request,meal_id:str):
 if not is_admin(request):return RedirectResponse("/admin/login",status_code=303)
 meal=get_item("meals",meal_id)
 if meal:save_item("meals",meal_id,{"active":not meal.get("active",True)})
 return RedirectResponse("/admin/menu",status_code=303)

@app.get("/admin/settings",response_class=HTMLResponse)
async def admin_settings(request:Request):
 if not is_admin(request):return RedirectResponse("/admin/login",status_code=303)
 configured=line_configured()
 if configured:ensure_line_pairing_code()
 ensure_availability_model()
 pickup_dates=list_collection("pickup_dates"); locations=list_collection("locations")
 for location in locations:
  period_keys=set()
  for key in location.get("slot_keys") or []:
   try:key_date,key_slot=key.split("|",1)
   except ValueError:continue
   if key_slot in MORNING_PICKUP_SLOTS:period_keys.add(f"{key_date}|morning")
   if key_slot in AFTERNOON_PICKUP_SLOTS:period_keys.add(f"{key_date}|afternoon")
  location["period_keys"]=sorted(period_keys)
 return render(request,"admin_settings.html",settings=get_settings(),pickup_dates=pickup_dates,locations=locations,stores=list_collection("stores"),line_configured=configured)

@app.post("/admin/settings")
async def settings_save(request:Request,headline:str=Form(...),ordering_open:str|None=Form(None)):
 if not is_admin(request):return RedirectResponse("/admin/login",status_code=303)
 save_settings({"headline":headline.strip(),"ordering_open":ordering_open=="on"}); return RedirectResponse("/admin/settings",status_code=303)

@app.post("/admin/pickup-dates/save")
async def pickup_date_save(request:Request,pickup_date_id:str=Form(""),date:str=Form(""),dates:str=Form(""),morning_open:str|None=Form(None),afternoon_open:str|None=Form(None),active:str|None=Form(None),sort:int=Form(99)):
 if not is_admin(request):return RedirectResponse("/admin/login",status_code=303)
 morning_enabled=morning_open=="on"; afternoon_enabled=afternoon_open=="on"
 slots=fixed_pickup_slots(morning_enabled,afternoon_enabled)
 selected_dates=[]
 for value in ([date] if date else [])+dates.split(","):
  value=value.strip()
  try:parsed_date=datetime.strptime(value,"%Y-%m-%d").date()
  except ValueError:continue
  if parsed_date<datetime.now(TAIPEI_TZ).date():continue
  if value not in selected_dates:selected_dates.append(value)
 if not selected_dates:return RedirectResponse("/admin/settings",status_code=303)
 existing_dates={item.get("date"):item for item in list_collection("pickup_dates")}
 for index,selected_date in enumerate(sorted(selected_dates)):
  existing=existing_dates.get(selected_date)
  item_id=pickup_date_id if pickup_date_id and len(selected_dates)==1 else (existing["id"] if existing else f"date-{secrets.token_hex(4)}")
  save_item("pickup_dates",item_id,{"date":selected_date,"pickup_slots":slots,"morning_open":morning_enabled,"afternoon_open":afternoon_enabled,"fixed_periods_v1":True,"active":active=="on" and bool(slots),"sort":sort+index})
 return RedirectResponse("/admin/settings",status_code=303)

@app.post("/admin/schedules/save")
async def schedule_save(request:Request,schedule_id:str=Form(""),date:str=Form(...),location_id:str=Form(...),pickup_slots:str=Form(...),active:str|None=Form(None),sort:int=Form(99),store_ids:list[str]=Form([])):
 if not is_admin(request):return RedirectResponse("/admin/login",status_code=303)
 location=get_item("locations",location_id)
 if not location:return RedirectResponse("/admin/settings",status_code=303)
 slots=[slot.strip() for slot in pickup_slots.replace("，",",").split(",") if slot.strip()]
 if not slots:slots=DEFAULT_PICKUP_SLOTS
 existing=next((item for item in list_schedules() if item.get("date")==date and item.get("location_id")==location_id),None)
 schedule_id=schedule_id or (existing["id"] if existing else f"schedule-{secrets.token_hex(4)}")
 valid_store_ids={item["id"] for item in list_collection("stores")}; selected_stores=[item for item in store_ids if item in valid_store_ids]
 save_schedule(schedule_id,{"date":date,"location_id":location_id,"location_name":location["name"],"pickup_slots":slots,"store_ids":selected_stores,"stores_configured":True,"active":active=="on","sort":sort})
 return RedirectResponse("/admin/settings",status_code=303)

@app.post("/admin/locations/save")
async def location_save(request:Request,location_id:str=Form(""),name:str=Form(...),pickup_slots:str=Form(""),slot_keys:list[str]=Form([]),period_keys:list[str]=Form([]),active:str|None=Form(None),sort:int=Form(99)):
 if not is_admin(request):return RedirectResponse("/admin/login",status_code=303)
 date_configs={config.get("date",""):config for config in list_collection("pickup_dates")}
 valid_keys={slot_key(date,slot) for date,config in date_configs.items() for slot in config.get("pickup_slots") or []}
 selected_keys={key for key in slot_keys if key in valid_keys}
 for period_key in period_keys:
  try:period_date,period=period_key.split("|",1)
  except ValueError:continue
  config=date_configs.get(period_date)
  if not config:continue
  period_slots=MORNING_PICKUP_SLOTS if period=="morning" else AFTERNOON_PICKUP_SLOTS if period=="afternoon" else []
  selected_keys.update(slot_key(period_date,slot) for slot in period_slots if slot in (config.get("pickup_slots") or []))
 selected_keys=sorted(selected_keys)
 location_id=location_id or f"loc-{secrets.token_hex(4)}"; save_item("locations",location_id,{"name":name.strip(),"slot_keys":selected_keys,"availability_configured":True,"periods_configured":True,"active":active=="on","sort":sort}); return RedirectResponse("/admin/settings",status_code=303)

@app.post("/admin/locations/{location_id}/toggle")
async def location_toggle(request:Request,location_id:str):
 if not is_admin(request):return RedirectResponse("/admin/login",status_code=303)
 loc=get_item("locations",location_id)
 if loc:save_item("locations",location_id,{"active":not loc.get("active",True)})
 return RedirectResponse("/admin/settings",status_code=303)

@app.post("/admin/stores/save")
async def store_save(request:Request,store_id:str=Form(""),name:str=Form(...),logo_url:str=Form(""),logo_file:UploadFile|None=None,active:str|None=Form(None),sort:int=Form(99),location_ids:list[str]=Form([])):
 if not is_admin(request):return RedirectResponse("/admin/login",status_code=303)
 store_id=store_id or stable_store_id(name); store_name=name.strip(); existing=get_item("stores",store_id) or {}; uploaded=await upload_image(logo_file)
 valid_location_ids={item["id"] for item in list_collection("locations")}; selected_locations=sorted({item for item in location_ids if item in valid_location_ids})
 save_item("stores",store_id,{"name":store_name,"logo_url":uploaded or logo_url.strip() or existing.get("logo_url",""),"location_ids":selected_locations,"locations_configured":True,"active":active=="on","sort":sort})
 for meal in list_collection("meals"):
  if meal.get("store_id")==store_id:save_item("meals",meal["id"],{"store":store_name})
 return RedirectResponse("/admin/settings",status_code=303)

@app.post("/admin/stores/{store_id}/toggle")
async def store_toggle(request:Request,store_id:str):
 if not is_admin(request):return RedirectResponse("/admin/login",status_code=303)
 store=get_item("stores",store_id)
 if store:save_item("stores",store_id,{"active":not store.get("active",True)})
 return RedirectResponse("/admin/settings",status_code=303)

@app.get("/health")
async def health():return {"status":"ok","database":"firestore" if db else "memory"}

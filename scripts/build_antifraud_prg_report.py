#!/usr/bin/env python3
import csv, html, ipaddress, json, re
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; RAW=ROOT/'antifraud_prg_53197618/raw'; OUT=ROOT/'antifraud_prg_53197618'

def n(v):
 try:return float(str(v or '').replace('\u00a0','').replace(' ','').replace(',','.'))
 except:return 0.0

def sec(v):
 p=[n(x) for x in str(v or '0').split(':')]
 return (p[-1] if p else 0)+(p[-2]*60 if len(p)>1 else 0)+(p[-3]*3600 if len(p)>2 else 0)

def pct(v,d=1):return f'{v*100:.{d}f}%'.replace('.',',')
def dur(v):
 v=int(round(v)); h,v=divmod(v,3600);m,s=divmod(v,60);return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'
def integer(v):return f'{int(round(v)):,}'.replace(',',' ')
def esc(v):return html.escape(str(v or ''))
def source(v):
 v=str(v or '').strip();return 'Не определено' if not v or v.lower() in ('не определено','undefined','none') else v.lower()
def subnet(v):
 s=str(v or '').replace('xxx','0')
 try:
  ip=ipaddress.ip_address(s);return str(ipaddress.ip_network(f'{ip}/{24 if ip.version==4 else 64}',strict=False))
 except:return s

def rows(path):
 with open(path,encoding='utf-8-sig',newline='') as f:r=list(csv.DictReader(f))
 return (r[0],r[1:]) if r and str(r[0].get('UTM Source','')).startswith('Итого') else ({},r)

def bucket():return defaultdict(float)
def add(b,r):
 v=n(r['Визиты']);u=n(r['Посетители']);b['v']+=v;b['u']+=u;b['br']+=n(r['Отказы'])*v;b['t']+=sec(r['Время на сайте'])*v;b['new']+=n(r['Доля новых посетителей'])*u;b['q']+=n(r['Конверсия (Первично-качественные звонки UIS)'])*v;b['p']+=n(r['Конверсия (Первичные звонки UIS)'])*v
def finish(b):
 v=b['v'] or 1;u=b['u'] or 1;return {'visits':b['v'],'users':b['u'],'bounce':b['br']/v,'time':b['t']/v,'new':b['new']/u,'q':b['q']/v,'p':b['p']/v}
def top(m,total):
 if not m:return ('—',0,0)
 k,v=max(m.items(),key=lambda x:x[1]);return(k,v,v/(total or 1))

def main():
 meta=json.loads((RAW/'meta.json').read_text());tt,tr=rows(RAW/'tech.csv');it,ir=rows(RAW/'ip.csv')
 base={'visits':n(tt['Визиты']),'bounce':n(tt['Отказы']),'time':sec(tt['Время на сайте']),'new':n(tt['Доля новых посетителей']),'q':n(tt['Конверсия (Первично-качественные звонки UIS)']),'p':n(tt['Конверсия (Первичные звонки UIS)'])}
 S={}
 def obj(s):return S.setdefault(s,{'b':bucket(),'ib':bucket(),'browser':defaultdict(float),'os':defaultdict(float),'model':defaultdict(float),'res':defaultdict(float),'ip':defaultdict(float),'net':defaultdict(float),'v6':0,'tf':[],'if':[]})
 for r in tr:
  s=source(r['UTM Source']);o=obj(s);v=n(r['Визиты']);add(o['b'],r)
  vals=(r['Версия браузера'] or 'Не определено',r['Операционная система (детально)'] or 'Не определено',r['Модель устройства'] or 'Не определено',r['Разрешение'] or 'Не определено')
  for k,x in zip(('browser','os','model','res'),vals):o[k][x]+=v
  br=n(r['Отказы']);t=sec(r['Время на сайте']);bn=vals[0].lower();reasons=[]
  if 'headless' in bn:reasons.append('headless-браузер')
  if br>=.82 and t<=30:reasons.append('высокий отказ + короткий визит')
  if ('не определ' in bn or 'другие' in bn) and br>=.95 and t<=5:reasons.append('unknown-браузер + почти 100% отказов')
  if br<=.01 and v>=150:reasons.append('аномально низкий отказ')
  if reasons:o['tf'].append((v,vals,br,t,reasons))
 for r in ir:
  s=source(r['UTM Source']);o=obj(s);v=n(r['Визиты']);add(o['ib'],r);ip=r['IP-адрес'] or 'Не определено';net=subnet(ip);o['ip'][ip]+=v;o['net'][net]+=v;o['v6']+=v if ':' in ip else 0
  br=n(r['Отказы']);t=sec(r['Время на сайте']);reasons=[]
  if br>=.82 and t<=30:reasons.append('плохое поведение IP-среза')
  if br<=.01 and v>=150:reasons.append('нулевой/почти нулевой отказ')
  if reasons:o['if'].append((v,ip,net,br,t,reasons))
 out=[]
 for s,o in S.items():
  m=finish(o['b']);im=finish(o['ib']);v=m['visits'] or im['visits'];
  if not v:continue
  tb,to,tm,tre=[top(o[k],v) for k in ('browser','os','model','res')];tip=top(o['ip'],im['visits']);tn=top(o['net'],im['visits']);v6=o['v6']/(im['visits'] or 1)
  tf=sorted(o['tf'],reverse=True)[:5];iff=sorted(o['if'],reverse=True)[:5];tfshare=sum(x[0] for x in tf)/v;ifshare=sum(x[0] for x in iff)/(im['visits'] or 1)
  sc=0;why=[]
  if m['bounce']>=.7 or m['bounce']-base['bounce']>=.25:sc+=24;why.append('сильно повышенный отказ')
  elif m['bounce']>=.55 or m['bounce']-base['bounce']>=.15:sc+=15;why.append('повышенный отказ')
  elif m['bounce']>=.48:sc+=7;why.append('отказ выше среднего')
  if m['time']<=30:sc+=20;why.append('очень короткое время')
  elif m['time']<=60:sc+=12;why.append('короткое время')
  elif m['time']<=90:sc+=6
  if m['new']>=.995:sc+=10;why.append('почти весь трафик новый')
  elif m['new']>=.98:sc+=6
  if base['q'] and m['q']<base['q']*.15 and v>=1500:sc+=7;why.append('почти нет качественных звонков')
  if base['p'] and m['p']>base['p']*4 and v>=500:sc+=10;why.append('аномально высокая первичная конверсия')
  if any('headless' in ' '.join(x[4]) for x in tf):sc+=30;why.append('headless-срез')
  elif tfshare>=.1:sc+=22;why.append('крупный технический аномальный срез')
  elif tfshare>=.03:sc+=14;why.append('технические аномалии')
  elif tf:sc+=6
  if tb[2]>=.6:sc+=8;why.append('концентрация браузера')
  if tre[2]>=.5:sc+=7;why.append('однотипное разрешение')
  if tn[2]>=.2:sc+=16;why.append('концентрация подсети')
  elif tn[2]>=.1:sc+=9
  elif tn[2]>=.05:sc+=4
  if ifshare>=.1:sc+=14;why.append('крупный аномальный IP-срез')
  elif ifshare>=.03:sc+=8;why.append('аномальные IP-срезы')
  elif iff:sc+=3
  if v6>=.5 and (m['bounce']>base['bounce']+.1 or tfshare>.03):sc+=4
  sc=min(100,sc);risk='Высокий' if sc>=60 else 'Средний' if sc>=35 else 'Низкий'
  out.append({'s':s,'m':m,'im':im,'score':sc,'risk':risk,'why':why,'tb':tb,'to':to,'tm':tm,'tr':tre,'tip':tip,'tn':tn,'v6':v6,'tf':tf,'iff':iff})
 out.sort(key=lambda x:(-x['score'],-x['m']['visits']))
 counts={x:sum(1 for r in out if r['risk']==x) for x in ('Высокий','Средний','Низкий')}; highv=sum(r['m']['visits'] for r in out if r['risk']=='Высокий')/(base['visits'] or 1)
 covt=sum(n(r['Визиты']) for r in tr)/(base['visits'] or 1);covi=sum(n(r['Визиты']) for r in ir)/(base['visits'] or 1)
 def flags(items,tech=True,total=1):
  z=[]
  for x in items:
   if tech:v,vals,br,t,why=x;label=' · '.join(vals[:2]+(vals[3],))
   else:v,ip,net,br,t,why=x;label=f'{ip} · {net}'
   z.append(f'<li><b>{esc(label)}</b><span>{integer(v)} визитов · {pct(v/(total or 1))} · отказы {pct(br)} · {dur(t)}</span><em>{esc("; ".join(why))}</em></li>')
  return ''.join(z) or '<li class="empty">Выраженных аномалий не найдено.</li>'
 cards=[]
 for i,r in enumerate(out):
  m=r['m'];cl={'Высокий':'high','Средний':'medium','Низкий':'low'}[r['risk']];op=' open' if i<6 or r['risk']!='Низкий' else ''
  action='Запросить плейсменты, SSP/apps/sites и user-agent; подтвердить аномальные сегменты перед исключением или претензией.' if r['risk']=='Высокий' else ('Точечно проверить отмеченные браузеры, разрешения и IP/подсети; площадку целиком пока не отключать.' if r['risk']=='Средний' else 'Оставить в мониторинге и контролировать появление новых технических кластеров.')
  cards.append(f'''<details class="card {cl}"{op}><summary><div><small>UTM Source</small><h2>{esc(r['s'])}</h2><p>{esc(' · '.join(r['why'][:4]) or 'критичных сочетаний не найдено')}</p></div><div class="risk"><b>{r['risk']} риск</b><strong>{r['score']}<i>/100</i></strong></div></summary><div class="body"><div class="metrics"><div><b>{integer(m['visits'])}</b><span>визиты</span></div><div><b>{pct(m['bounce'])}</b><span>отказы</span></div><div><b>{dur(m['time'])}</b><span>время</span></div><div><b>{pct(m['new'])}</b><span>новые</span></div><div><b>{pct(m['q'],3)}</b><span>ПК-звонки</span></div><div><b>{pct(m['p'],3)}</b><span>первичные</span></div></div><div class="twocol"><section><h3>IP / подсети</h3><p><b>IPv6:</b> {pct(r['v6'])}</p><p><b>Топ IP:</b> {esc(r['tip'][0])} · {pct(r['tip'][2])}</p><p><b>Топ подсеть:</b> {esc(r['tn'][0])} · {pct(r['tn'][2])}</p></section><section><h3>Технический профиль</h3><p><b>Браузер:</b> {esc(r['tb'][0])} · {pct(r['tb'][2])}</p><p><b>ОС:</b> {esc(r['to'][0])} · {pct(r['to'][2])}</p><p><b>Модель:</b> {esc(r['tm'][0])} · {pct(r['tm'][2])}</p><p><b>Разрешение:</b> {esc(r['tr'][0])} · {pct(r['tr'][2])}</p></section></div><div class="twocol anomalies"><section><h3>Технические аномалии</h3><ul>{flags(r['tf'],True,m['visits'])}</ul></section><section><h3>IP-аномалии</h3><ul>{flags(r['iff'],False,r['im']['visits'])}</ul></section></div><div class="action"><b>Рекомендация</b><p>{esc(action)}</p></div></div></details>''')
 topnames=', '.join(r['s'] for r in out if r['risk']!='Низкий')[:220] or 'критичных площадок не выявлено'
 sampled='без семплирования' if not any(meta.get('sampled',{}).values()) else 'частично семплировано'
 css='''*{box-sizing:border-box}body{margin:0;font:14px Arial,sans-serif;color:#20233a;background:linear-gradient(145deg,#fff,#f5f2ec);line-height:1.55}.hero{padding:52px 24px 42px;background:linear-gradient(135deg,#fff 20%,#dfeef7);border-bottom:1px solid #e7e2d9}.wrap,main{max-width:1240px;margin:auto}.brand{font-size:12px;font-weight:800;letter-spacing:.1em;text-transform:uppercase}.hero h1{font-size:clamp(36px,6vw,68px);line-height:1.02;letter-spacing:-.055em;margin:34px 0 15px}.hero p{font-size:18px;color:#737785;max-width:900px}.tags{display:flex;flex-wrap:wrap;gap:8px;margin-top:22px}.tag{padding:8px 12px;border-radius:999px;background:#ffffffb8;border:1px solid #20233a20;font-size:12px;font-weight:700}main{padding:30px 20px 70px}.panel{background:#fff;border:1px solid #e7e2d9;border-radius:25px;padding:27px;margin-bottom:22px;box-shadow:0 18px 60px #20233a12}.panel h2{font-size:28px;margin:0 0 9px}.lead{color:#737785}.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:11px;margin-top:20px}.kpi{padding:17px;border-radius:17px;background:#f8f7f4;border:1px solid #e7e2d9}.kpi b{display:block;font-size:27px}.kpi span,.metrics span{font-size:11px;color:#737785}.method{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:20px}.step{padding:18px;border:1px solid #e7e2d9;border-radius:17px;background:#faf9f6}.step i{display:grid;place-items:center;width:30px;height:30px;background:#20233a;color:white;border-radius:9px;font-style:normal;font-weight:bold}.step h3{margin:14px 0 6px}.step p{margin:0;color:#737785;font-size:13px}.coverage{margin-top:18px;padding:15px;border-left:4px solid #a76a16;background:#fff8ec}.title{display:flex;justify-content:space-between;align-items:end;margin:34px 3px 14px}.title h2{font-size:31px;margin:0}.card{background:#fff;border:1px solid #e7e2d9;border-left:5px solid #2f7555;border-radius:21px;margin-bottom:13px;overflow:hidden;box-shadow:0 10px 35px #20233a0e}.card.medium{border-left-color:#a76a16}.card.high{border-left-color:#bb3030}.card summary{cursor:pointer;list-style:none;padding:21px 23px;display:flex;justify-content:space-between;gap:20px}.card summary::-webkit-details-marker{display:none}.card small{color:#737785;text-transform:uppercase;letter-spacing:.1em;font-weight:bold}.card h2{font-size:26px;margin:2px 0}.card summary p{margin:0;color:#737785;font-size:13px}.risk{text-align:right}.risk b{display:block;font-size:12px}.risk strong{font-size:30px}.risk i{font-size:12px;color:#737785;font-style:normal}.body{padding:0 23px 23px;border-top:1px solid #e7e2d9}.metrics{display:grid;grid-template-columns:repeat(6,1fr);gap:9px;padding:17px 0}.metrics div{padding:12px;background:#f8f7f4;border-radius:13px}.metrics b{display:block;font-size:20px}.twocol{display:grid;grid-template-columns:1fr 1fr;gap:11px}.twocol section{padding:17px;border:1px solid #e7e2d9;border-radius:16px}.twocol h3{margin:0 0 9px}.twocol p{margin:6px 0;font-size:13px}.anomalies{margin-top:11px}ul{list-style:none;padding:0;margin:0;display:grid;gap:8px}li{padding:10px 11px;background:#f8f7f4;border-radius:11px;font-size:12px}li b,li span,li em{display:block}li span{color:#737785}li em{color:#bb3030;font-style:normal}.empty{color:#737785}.action{margin-top:11px;padding:15px 17px;background:#edf6f0;border-radius:15px}.action p{margin:4px 0 0}.footer{color:#737785;font-size:12px;padding:10px 2px}@media(max-width:900px){.kpis{grid-template-columns:repeat(2,1fr)}.method{grid-template-columns:repeat(2,1fr)}.metrics{grid-template-columns:repeat(3,1fr)}}@media(max-width:650px){.hero{padding:35px 18px}main{padding:20px 11px 50px}.panel{padding:19px}.method,.twocol{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(2,1fr)}}'''
 report=f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Antifraud PRG 53197618</title><style>{css}</style></head><body><header class="hero"><div class="wrap"><div class="brand">Level Group · Metrica intelligence</div><h1>Комплексный антифрод-анализ PRG-трафика</h1><p>Счётчик 53197618 · май–июнь 2026 · UTM Campaign содержит «prg». Поведение, звонки, IP/подсети, браузеры, ОС, модели и разрешения сведены в единый профиль каждой площадки.</p><div class="tags"><span class="tag">01.05–30.06.2026</span><span class="tag">{sampled}</span><span class="tag">скоринг ≠ доказательство фрода</span></div></div></header><main><section class="panel"><h2>Главный вывод</h2><p class="lead">Проанализировано {integer(base['visits'])} PRG-визитов. Высокий риск: {counts['Высокий']} источников, средний: {counts['Средний']}. Приоритет проверки: <b>{esc(topnames)}</b>.</p><div class="kpis"><div class="kpi"><b>{integer(base['visits'])}</b><span>PRG-визиты</span></div><div class="kpi"><b>{pct(base['bounce'])}</b><span>средний отказ</span></div><div class="kpi"><b>{dur(base['time'])}</b><span>среднее время</span></div><div class="kpi"><b>{counts['Высокий']}</b><span>high-risk источников</span></div><div class="kpi"><b>{pct(highv)}</b><span>визитов high-risk</span></div></div><div class="coverage"><b>Покрытие:</b> технический файл — {pct(covt)}, IP-файл — {pct(covi)}. Неполное покрытие означает приоритизацию для ручной проверки, а не окончательный вердикт.</div></section><section class="panel"><h2>Методика: как работает проверка</h2><p class="lead">Риск формируется только при совпадении нескольких независимых сигналов внутри одной площадки.</p><div class="method"><div class="step"><i>1</i><h3>Площадка целиком</h3><p>Визиты, отказы, время, новые посетители и звонковые цели.</p></div><div class="step"><i>2</i><h3>Внутренние срезы</h3><p>Браузеры, ОС, модели, разрешения, IP и подсети.</p></div><div class="step"><i>3</i><h3>Комбинация сигналов</h3><p>Один показатель не считается доказательством.</p></div><div class="step"><i>4</i><h3>Действие</h3><p>Мониторинг, точечная проверка или претензия после подтверждения.</p></div></div></section><div class="title"><h2>Анализ по площадкам</h2><span>{len(out)} источников</span></div>{''.join(cards)}<div class="footer">Яндекс Метрика · счётчик 53197618 · 01.05–30.06.2026 · UTM Campaign содержит «prg». IP маскируются Метрикой. Скоринг служит для приоритизации и не доказывает фрод автоматически.</div></main></body></html>'''
 OUT.mkdir(exist_ok=True);(OUT/'antifraud_report.html').write_text(report,encoding='utf-8');(OUT/'summary.json').write_text(json.dumps({'base':base,'counts':counts,'coverage':{'tech':covt,'ip':covi},'sources':out},ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'sources':len(out),'counts':counts,'coverage':[covt,covi]},ensure_ascii=False))
if __name__=='__main__':main()

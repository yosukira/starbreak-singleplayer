"""Dependency-free battle history tab for the bundled native Tk editor."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog

from localserver.battle_records import RecordStore, CLASSES, MAX_IMPORT
from localserver import paths, equipment


class BattlePanel(ttk.Frame):
    def __init__(self, parent, language='en'):
        super().__init__(parent, padding=14, style='Battle.TFrame')
        self.language = language
        self.store = RecordStore()
        self.rows, self.visible_rows = [], []
        self.catalogue = equipment.load_catalogue()
        self.refresh_after = None
        self.vars = {key: tk.StringVar() for key in ('from', 'to', 'class', 'result', 'map')}
        style = ttk.Style(self)
        style.configure('Battle.TFrame', background='#101923')
        style.configure('Battle.TLabel', background='#101923', foreground='#b3c5d4')
        style.configure('BattleTitle.TLabel', background='#101923', foreground='#dbe7ef', font=('Arial', 18, 'bold'))
        style.configure('Battle.Treeview', background='#14212d', fieldbackground='#14212d', foreground='#c7d9e8', rowheight=38)
        style.map('Battle.Treeview', background=[('selected', '#2e4d64')])
        top = ttk.Frame(self, style='Battle.TFrame'); top.pack(fill='x')
        ttk.Label(top, text=self.t('战斗记录', 'Battle records'), style='BattleTitle.TLabel').pack(side='left')
        ttk.Button(top, text=self.t('导出所选', 'Export selected'), command=self.export).pack(side='right', padx=4)
        ttk.Button(top, text=self.t('导入记录', 'Import'), command=self.import_file).pack(side='right', padx=4)
        filters = ttk.Frame(self, style='Battle.TFrame'); filters.pack(fill='x', pady=14)
        for index, (key, title, values) in enumerate((
            ('from', self.t('开始日期', 'From'), None), ('to', self.t('结束日期', 'To'), None),
            ('class', self.t('角色', 'Class'), ['', *CLASSES.values()]),
            ('result', self.t('结果', 'Result'), ['', *[self.result(r) for r in ('victory', 'defeat', 'interrupted', 'active')]]))):
            ttk.Label(filters, text=title, style='Battle.TLabel').grid(row=0, column=index, sticky='w')
            if values is None:
                widget = ttk.Entry(filters, textvariable=self.vars[key], width=12)
            else:
                widget = ttk.Combobox(filters, textvariable=self.vars[key], values=values, state='readonly', width=14)
            widget.grid(row=1, column=index, sticky='ew', padx=(0, 9), pady=(5, 0))
        ttk.Label(filters, text=self.t('搜索地图名称 / ID', 'Search map name / ID'), style='Battle.TLabel').grid(row=0, column=4, sticky='w')
        ttk.Entry(filters, textvariable=self.vars['map'], width=24).grid(row=1, column=4, sticky='ew', pady=(5, 0))
        filters.columnconfigure(4, weight=1)
        presets = ttk.Frame(self, style='Battle.TFrame'); presets.pack(fill='x')
        for days, title in ((1, self.t('今天', 'Today')), (0, self.t('昨天', 'Yesterday')), (7, self.t('近 7 天', '7 days')), (30, self.t('近 30 天', '30 days')), (None, self.t('全部日期', 'All time'))):
            ttk.Button(presets, text=title, command=lambda d=days:self.dates(d)).pack(side='left', padx=(0, 5))
        ttk.Button(presets, text=self.t('重置筛选', 'Reset filters'), command=self.reset).pack(side='right')
        self.summary = ttk.Label(self, style='BattleTitle.TLabel'); self.summary.pack(fill='x', pady=18)
        body = ttk.Frame(self, style='Battle.TFrame'); body.pack(fill='both', expand=True)
        columns = ('result', 'class', 'map', 'time', 'duration', 'damage', 'kills', 'gens', 'pickups')
        self.table = ttk.Treeview(body, columns=columns, show='headings', selectmode='extended', style='Battle.Treeview')
        labels = [self.t('结果','Result'), self.t('角色','Class'), self.t('地图','Map'), self.t('日期时间','Date / time'), self.t('用时','Duration'), self.t('伤害','Damage'), self.t('击杀','Kills'), 'Gen', self.t('血包 / 弹药','HP / ammo')]
        for key, label, width in zip(columns, labels, (90,100,240,150,70,100,65,55,90)):
            self.table.heading(key, text=label); self.table.column(key, width=width, minwidth=55, stretch=key=='map')
        self.table.tag_configure('victory', foreground='#b7e98b')
        self.table.tag_configure('defeat', foreground='#e99e9e')
        self.table.tag_configure('interrupted', foreground='#93a5b7')
        y = ttk.Scrollbar(body, orient='vertical', command=self.table.yview)
        x = ttk.Scrollbar(body, orient='horizontal', command=self.table.xview)
        self.table.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        self.table.grid(row=0,column=0,sticky='nsew'); y.grid(row=0,column=1,sticky='ns'); x.grid(row=1,column=0,sticky='ew')
        body.columnconfigure(0,weight=1); body.rowconfigure(0,weight=1)
        self.table.bind('<Double-1>', lambda _:self.detail())
        self.table.bind('<Return>', lambda _:self.detail())
        bottom=ttk.Frame(self,style='Battle.TFrame'); bottom.pack(fill='x',pady=(12,0))
        ttk.Button(bottom,text=self.t('查看详情','View details'),command=self.detail).pack(side='left')
        self.notice=ttk.Label(bottom,style='Battle.TLabel',wraplength=720);self.notice.pack(side='left',padx=14)
        self.notice.configure(text=self.t('双击查看装备与参数。Ctrl / Shift 可多选。— 表示未记录。','Double-click for equipment and parameters. Ctrl / Shift selects multiple battles. — means not recorded.'))
        for variable in self.vars.values(): variable.trace_add('write', lambda *_:self.filter())
        self.bind('<Destroy>',self.destroyed,add='+')
        self.filter()
        self.refresh()

    def t(self, zh, en):return zh if self.language=='zh' else en
    def result(self, value):return {'victory':self.t('胜利','Victory'),'defeat':self.t('失败','Defeat'),'interrupted':self.t('中断','Interrupted'),'active':self.t('进行中','In progress')}[value]
    @staticmethod
    def n(value):return '—' if value is None else f'{value:,.0f}'
    @staticmethod
    def duration(value):return f'{int(value)//60}:{int(value)%60:02d}'

    def dates(self, days):
        today=datetime.now().date()
        if days is None:start=end=''
        else:
            end=today-timedelta(days=1 if days==0 else 0)
            start=end-timedelta(days=max(0,(days or 1)-1));start,end=start.isoformat(),end.isoformat()
        self.vars['from'].set(start);self.vars['to'].set(end)

    def reset(self):
        for variable in self.vars.values():variable.set('')

    def refresh(self):
        try:
            fresh=self.store.list()
            if fresh!=self.rows:self.rows=fresh;self.filter()
        except Exception:
            self.notice.configure(text=self.t('暂时无法读取记录。','Could not read records.'))
        self.refresh_after=self.after(5000,self.refresh)

    def destroyed(self,event):
        if event.widget is self and self.refresh_after:
            self.after_cancel(self.refresh_after);self.refresh_after=None

    def filter(self):
        values={key:var.get().strip() for key,var in self.vars.items()}
        for key in ('from','to'):
            if values[key]:
                try:datetime.strptime(values[key],'%Y-%m-%d')
                except ValueError:
                    self.notice.configure(text=self.t('日期格式为 YYYY-MM-DD。','Use YYYY-MM-DD for dates.'));return
        rows=[]
        for row in self.rows:
            date=datetime.fromtimestamp(row['started']).strftime('%Y-%m-%d')
            if values['from'] and date<values['from'] or values['to'] and date>values['to']:continue
            if values['class'] and CLASSES[row['shell']]!=values['class']:continue
            if values['result'] and self.result(row['result'])!=values['result']:continue
            if values['map'].lower() not in (row['map']+' '+row['map_title']).lower():continue
            rows.append(row)
        self.visible_rows=rows
        selected=set(self.table.selection());position=self.table.yview()[0]
        self.table.delete(*self.table.get_children())
        for row in rows:
            m=row['metrics']
            self.table.insert('', 'end', iid=row['id'], tags=(row['result'],), values=(self.result(row['result']),CLASSES[row['shell']],row['map_title'],datetime.fromtimestamp(row['started']).strftime('%Y-%m-%d %H:%M'),self.duration(row['duration']),self.n(m['damage_dealt']),self.n(m['kills']),self.n(m.get('generators_destroyed')),self.n(m['health_packs'])+' / '+self.n(m['ammo_packs'])))
        self.table.selection_set([row['id'] for row in rows if row['id'] in selected]);self.table.yview_moveto(position)
        wins=sum(r['result']=='victory' for r in rows);losses=sum(r['result']=='defeat' for r in rows)
        rate=f'{100*wins/(wins+losses):.0f}%' if wins+losses else '—'
        damage=sum(r['metrics']['damage_dealt'] or 0 for r in rows) if any(r['metrics']['damage_dealt'] is not None for r in rows) else None
        self.summary.configure(text=f"{self.t('场次','Battles')}  {len(rows)}     {self.t('胜率','Win rate')}  {rate}     {self.t('伤害','Damage')}  {self.n(damage)}")

    def detail(self):
        selected=self.table.selection()
        if not selected:return
        row=next(r for r in self.rows if r['id']==selected[0])
        window=tk.Toplevel(self);window.title(self.t('战斗详情','Battle details'));window.geometry('850x680');window.configure(bg='#101923');window.attributes('-topmost',True)
        box=tk.Text(window,bg='#101923',fg='#c7d9e8',font=('Arial',12),padx=24,pady=20,wrap='word',relief='flat');box.pack(fill='both',expand=True)
        def item_name(item):
            frozen=next((i for i in row['parameters'].get('items', []) if i['type']==item['type_']),None)
            return (frozen or self.catalogue.get(item['type_'],{})).get('name',str(item['type_']))
        lines=[f"{CLASSES[row['shell']]} · {row['map_title']}",f"{self.result(row['result'])} · {self.duration(row['duration'])} · {row['version']}",'']
        if row.get('parent'):
            lines+=[f"{self.t('接管起点','Takeover point')}: {self.duration(row['parent']['at'])}",'']
        for key,label in [('damage_dealt',self.t('造成伤害','Damage dealt')),('kills',self.t('击杀','Kills')),('health_packs',self.t('血包','Health packs')),('ammo_packs',self.t('弹药包','Ammo packs')),('healing',self.t('恢复生命','Healing')),('damage_taken',self.t('承受伤害','Damage taken'))]:lines.append(f"{label}: {self.n(row['metrics'][key])}")
        if row['map'] in ('egy_hulk','hulk_normal','egy_run'):
            lines.append(f"{self.t('摧毁 Gen','Generators destroyed')}: {self.n(row['metrics'].get('generators_destroyed'))}")
            layout=row.get('turret_layout',[])
            if layout:
                summary='-'.join(str(sum(t['row']==side for t in layout))+'('+str(sum(t['row']==side and t['ammo']==('scattershot' if side=='front' else 'bossmissile') for t in layout))+')' for side in ('front','upper','lower'))
                lines.append(self.t('前(散射)-上(追踪弹)-下(追踪弹): ','Front (scatter) – Upper (missiles) – Lower (missiles): ')+summary)
        lines+=['',self.t('本场装备','Battle loadout'),f"MK{row['parameters']['loadout']['mk_']}"]
        for item in row['parameters']['loadout']['items_']:
            lines.append(f"\n{item_name(item)}  ·  ID {item['type_']}")
            lines.extend(f"  {equipment.STAT_NAMES.get(b['stat_'],str(b['stat_']))}: {b['val_']}" for b in item['statboosts_'])
        p=row['parameters'];labels={'balance':self.t('战斗参数','Battle parameters'),'drops':self.t('掉落','Drops'),'turrets':self.t('炮塔数量','Turret counts'),'missiles':self.t('追踪炮塔','Seeking turrets'),'projectile_speeds':self.t('本场弹速','Projectile speeds')}
        for group,title in labels.items():lines+=['',title,json.dumps(p[group],ensure_ascii=False,indent=2)]
        lines += ['',f"{self.t('跳过开局','Skip opening')}: {p['skip_intro']}",f"{self.t('追踪弹延迟','Homing delay')}: {p['missile_delay_ms']} ms",f"{self.t('随机种子','Random seed')}: {row['seed']}",f"{self.t('参数变更次数','Parameter changes')}: {len(row['changes'])}"]
        if row['changes']:lines+=['',json.dumps(row['changes'],ensure_ascii=False,indent=2)]
        if row.get('rooms'):
            lines+=['',self.t('房间历程','Visited rooms')]
            lines.extend(f"{self.duration(room['entered'])}  {room['role']}" for room in row['rooms'])
        lines+=['',self.t('录像回放与接管仍在开发。','Game replay and takeover are in development.')]
        box.insert('1.0','\n'.join(lines));box.configure(state='disabled')
        ttk.Button(window,text=self.t('关闭','Close'),command=window.destroy).pack(pady=10)

    def export(self):
        try:
            name,data=self.store.export(list(self.table.selection()))
            target=filedialog.asksaveasfilename(parent=self,initialfile=name,defaultextension=Path(name).suffix)
            if target:Path(target).write_bytes(data);self.notice.configure(text=self.t('记录已导出。','Records exported.'))
        except Exception:
            self.notice.configure(text=self.t('请选择 1–128 场已结束的战斗。','Select 1–128 finished battles.'))

    def import_file(self):
        chosen=filedialog.askopenfilename(parent=self,filetypes=[('StarBreak records','*.sbreplay *.zip')])
        if not chosen:return
        try:
            path=Path(chosen)
            if path.stat().st_size>MAX_IMPORT:raise ValueError('too large')
            result=self.store.import_bytes(path.read_bytes());self.rows=self.store.list();self.filter()
            self.notice.configure(text=self.t(f"已导入 {result['imported']} 场，重复 {result['duplicates']} 场。",f"Imported {result['imported']} battles; {result['duplicates']} duplicates."))
        except Exception:self.notice.configure(text=self.t('无效的战绩文件或 ZIP（最大 256 MB）。','Invalid battle file or ZIP (maximum 256 MB).'))

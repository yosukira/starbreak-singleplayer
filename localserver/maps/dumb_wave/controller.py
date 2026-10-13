"""Twenty opening patterns, then the final pattern with growing spawn budgets."""
import math
import random
from .scene import DumbWavePack
from ... import game_settings

class DumbWaveController:
    def __init__(self,pack=None,seed=0xC0FFEE):
        self.pack=pack or DumbWavePack();self.config=self.pack.waves;self.rng=random.Random(seed)
        self.spawn_rate=game_settings.balance('dumb_wave')['spawn_rate'] if self.pack.map['id']=='dumb_wave' else 1.
        self.prepare()

    def prepare(self):
        self.time=0.;self.wave=0;self.cycle=0;self.state='waiting';self.events=[]
        self.alive=set();self.pending=0;self.issued=0;self.created=0;self.next_spawn=math.inf
        self.until=math.inf;self.last_number=None;self.groups=[];self.group_index=0

    def emit(self,kind,**values):self.events.append(dict(kind=kind,at=self.time,**values))
    def take_events(self):events,self.events=self.events,[];return events

    def begin(self):
        self._countdown(1,self.config['waves'][0]['countdown_s'])
        return self.take_events()

    def pattern_wave(self,wave=None):
        return min(self.wave if wave is None else wave,self.config['wave_count'])

    def interval(self,wave=None):
        wave=self.wave if wave is None else wave
        row=self.config['waves'][self.pattern_wave(wave)-1];base=row['batch_interval_s']
        if row['batch_count']==1:return 0.
        extra=max(0,wave-self.config['wave_count'])
        loop=self.config['loop'];return max(loop['min_interval_s'],base-extra*loop['interval_decrement_s'])/self.spawn_rate

    def quota(self,wave=None):
        wave=self.wave if wave is None else wave
        if not wave:return 0
        base=self.config['waves'][self.pattern_wave(wave)-1]['spawn_total']
        loop=self.config['loop']
        return base+max(0,wave-self.config['wave_count'])*int(loop.get('spawn_increment_per_wave',0)) if loop['enabled'] else base

    def display_wave(self):return self.wave

    def _countdown(self,wave,seconds):
        self.wave=wave;self.cycle=(wave-1)//self.config['wave_count']
        self.state='countdown';self.until=self.time+seconds;self.last_number=None
        self._title_tick()

    def _title_tick(self):
        number=max(1,int(math.ceil(self.until-self.time-1e-8)))
        if number!=self.last_number:self.last_number=number;self.emit('title',text=str(number))

    def _start_wave(self):
        self.cycle=(self.wave-1)//self.config['wave_count']
        self.state='wave';self.row=self.config['waves'][self.pattern_wave()-1]
        self.alive=set();self.pending=0;self.issued=0;self.created=0
        count=self.quota();n=self.row['batch_count'];self.groups=[1]*n
        if self.wave>self.config['wave_count']:

            pool=self.row['batch_sizes'];self.groups=list(pool)
            remaining=count-sum(self.groups)
            while remaining>0:
                size=min(remaining,self.rng.choice(pool));self.groups.append(size);remaining-=size
            self.rng.shuffle(self.groups)
        elif 'batch_sizes'in self.row and count==self.row['spawn_total']:
            self.groups=list(self.row['batch_sizes']);self.rng.shuffle(self.groups)
        else:
            for _ in range(count-n):self.groups[self.rng.randrange(n)]+=1
        self.group_index=0;self.next_spawn=self.time+self.config['first_flash_delay_s']
        title=self.config.get('title_format','Wave {wave}').format(wave=self.display_wave())
        self.emit('title',text=title);self.emit('wave_start',wave=self.wave,pattern_wave=self.pattern_wave(),
            display_wave=self.display_wave(),cycle=self.cycle,total=count)

    def npc_created(self,identity):
        self.pending=max(0,self.pending-1);self.created+=1;self.alive.add(identity)

    def npc_died(self,identity):self.alive.discard(identity)
    def add_damage(self,*args):pass

    def step(self,dt):
        if not math.isfinite(dt)or dt<0:raise ValueError('dt must be finite and nonnegative')
        end=self.time+dt
        while self.time<end-1e-9:
            if self.state=='countdown':
                self._title_tick();number=max(1,math.ceil(self.until-self.time-1e-8))
                boundary=min(end,self.until,self.until-number+1)
                self.time=max(self.time+min(1e-8,end-self.time),boundary)
                if self.time>=self.until-1e-8:self.time=self.until;self._start_wave()
            elif self.state=='wave':
                while self.group_index<len(self.groups)and self.next_spawn<=self.time+1e-8:
                    amount=self.groups[self.group_index];self.group_index+=1;self.pending+=amount;self.issued+=amount
                    self.emit('spawn_batch',wave=self.wave,pattern_wave=self.pattern_wave(),cycle=self.cycle,count=amount)
                    self.next_spawn+=self.interval()
                if self.issued==self.quota()and not self.pending and not self.alive:
                    self.emit('wave_clear',wave=self.wave,pattern_wave=self.pattern_wave(),cycle=self.cycle)
                    if self.wave>=self.config['wave_count']:
                        if not self.config['loop']['enabled']:
                            self.state='done';self.emit('complete');self.time=end;break
                        self._countdown(self.wave+1,self.config['loop']['countdown_s'])
                    else:self._countdown(self.wave+1,self.config['waves'][self.wave]['countdown_s'])
                    continue
                self.time=min(end,self.next_spawn)if self.group_index<len(self.groups)else end
            else:self.time=end
        return self.take_events()

    def status(self):
        return dict(state=self.state,wave=self.display_wave(),pattern_wave=self.pattern_wave(),cycle=self.cycle+1,spawned=self.created,pending=self.pending,
            alive=len(self.alive),quota=self.quota(),
            interval_s=round(self.interval(),3)if self.wave else None)

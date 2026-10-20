"""Native execution journals use the same atomic local store as browser tapes."""
import hashlib
from pathlib import Path
import re
import uuid

from plugins import _native_replay as native
from tools.client_replay import ClientJournals,integer


def adapter_digest():
    root=Path(__file__).resolve().parents[1]
    value=hashlib.sha256()
    for name in ('_native_replay.py','_native_replay_hooks.py','_native_replay_capture.py','_native_replay_player.py'):
        value.update((root/'plugins'/name).read_bytes().replace(b'\r\n',b'\n').replace(b'\r',b'\n'))
    return value.hexdigest()


def compatible_adapter(recorded, current=None):
    current=adapter_digest() if current is None else current
    if recorded==current:return True
    import json
    try:
        aliases=json.loads((Path(__file__).resolve().parents[1]/'data/mechanics/native_replay_compatibility.json').read_text(encoding='utf8'))
        return recorded in aliases.get(current,[])
    except (OSError,ValueError,TypeError):return False


def document(value):
    if not isinstance(value,dict)or value.get('schema')!=native.SCHEMA:
        raise ValueError('Unsupported native client recording')
    viewport=value['viewport']
    if not isinstance(viewport,list)or len(viewport)!=2:raise ValueError('Invalid native replay viewport')
    for key in('client','adapter'):
        if not isinstance(value.get(key),str)or not re.fullmatch('[0-9a-f]{64}',value[key]):
            raise ValueError('Invalid native replay version digest')
    if value.get('preferences')is not None:raise ValueError('Native recordings do not include browser preferences')
    result=dict(schema=native.SCHEMA,id=str(uuid.UUID(value['id'])),client=value['client'],adapter=value['adapter'],
        preferences=None,viewport=[integer(v,1,16384)for v in viewport],ops=native.validate_operations(value['ops']))
    if 'clock_scope' in value:
        if value['clock_scope']!='game-v1':raise ValueError('Unsupported native clock scope')
        result['clock_scope']='game-v1'
    if 'runners' in value:
        rows=value['runners']
        if not isinstance(rows,list) or len(rows)>64:raise ValueError('Invalid portable replay engine history')
        result['runners']=[];previous=0
        for row in rows:
            cursor=integer(row['cursor'],previous+1,len(result['ops']))
            if result['ops'][cursor-1]!=['loop',1] or row.get('engine') not in ('browser','native'):raise ValueError('Invalid portable engine boundary')
            for key in ('client','adapter'):
                if not isinstance(row.get(key),str) or not re.fullmatch('[0-9a-f]{64}',row[key]):raise ValueError('Invalid portable engine version')
            result['runners'].append(dict(cursor=cursor,engine=row['engine'],client=row['client'],adapter=row['adapter']));previous=cursor
    return result


class NativeJournals(ClientJournals):
    schema=native.SCHEMA
    operation=staticmethod(native.operation)

    def mark_runner(self,branch,source,cursor,runner,engine='browser'):
        import json
        if engine not in ('browser','native'):raise ValueError('Invalid portable engine kind')
        rows=[row for row in source.get('runners',[]) if row['cursor']<cursor]
        rows.append(dict(cursor=cursor,engine=engine,client=runner['client'],adapter=runner['adapter']))
        if len(rows)>64:raise ValueError('Too many portable engine transitions')
        with self.connect() as db:
            row=db.execute('SELECT header FROM attempts WHERE id=?',(branch,)).fetchone()
            if row is None:raise ValueError('Unknown portable branch')
            header=json.loads(row[0]);header['runners']=rows
            db.execute('UPDATE attempts SET header=? WHERE id=?',(json.dumps(header,separators=(',',':')),branch))

    def begin(self,identifier,client,adapter,preferences,viewport):
        if preferences.get('content')is not None:raise ValueError('Native preferences must remain on the local client')
        for digest in(client,adapter):
            if not isinstance(digest,str)or not re.fullmatch('[0-9a-f]{64}',digest):raise ValueError('Invalid native engine digest')
        result=super().begin(identifier,client,adapter,preferences,viewport)
        import json
        with self.connect() as db:
            row=db.execute('SELECT header FROM attempts WHERE id=?',(identifier,)).fetchone()
            header=json.loads(row[0]);header['clock_scope']='game-v1'
            db.execute('UPDATE attempts SET header=? WHERE id=?',(json.dumps(header,separators=(',',':')),identifier))
        return dict(result,clock_scope='game-v1')

    def fork(self,identifier,cursor,source=None):
        source=self.read(identifier) if source is None else source
        branch=super().fork(identifier,cursor,source)
        if source.get('clock_scope')!='game-v1':
            import json
            with self.connect() as db:
                row=db.execute('SELECT header FROM attempts WHERE id=?',(branch['id'],)).fetchone()
                header=json.loads(row[0]);header.pop('clock_scope',None)
                db.execute('UPDATE attempts SET header=? WHERE id=?',(json.dumps(header,separators=(',',':')),branch['id']))
        return branch

    def read(self,identifier,require_complete=True):
        value=super().read(identifier,require_complete=require_complete)
        return document(value)if require_complete else value

    def page_header(self,identifier):
        """Validate the small completed header without materializing the tape."""
        import json
        identifier=str(uuid.UUID(identifier))
        with self.connect() as db:
            row=db.execute('SELECT header,sequence,complete FROM attempts WHERE id=?',(identifier,)).fetchone()
            count=db.execute('SELECT COUNT(*) FROM chunks WHERE id=?',(identifier,)).fetchone()[0]
        if row is None or not row[2]:raise ValueError('Native replay is incomplete')
        if count!=row[1] or count<1:raise ValueError('Native replay is damaged')
        header=json.loads(row[0]);runners=header.pop('runners',[])
        clean=document(dict(header,ops=[]));clean.pop('ops')
        if not isinstance(runners,list) or len(runners)>64:raise ValueError('Invalid portable engine history')
        previous=0
        for runner in runners:
            cursor=integer(runner['cursor'],previous+1,native.MAX_OPERATIONS)
            if runner.get('engine') not in ('browser','native'):raise ValueError('Invalid portable engine history')
            for key in ('client','adapter'):
                if not isinstance(runner.get(key),str) or not re.fullmatch('[0-9a-f]{64}',runner[key]):raise ValueError('Invalid portable engine version')
            previous=cursor
        if runners:clean['runners']=runners
        return clean,count

    def read_pages(self,identifier):
        """Verify chunk hashes and call boundaries with bounded working memory."""
        import json
        header,total=self.page_header(identifier);validator=native.OperationValidator()
        boundaries={row['cursor'] for row in header.get('runners',[])};count=0
        with self.connect() as db:
            rows=db.execute('SELECT sequence,digest,data FROM chunks WHERE id=? ORDER BY sequence',(identifier,))
            for expected in range(total):
                row=rows.fetchone()
                if row is None or row[0]!=expected:raise ValueError('Native replay sequence changed')
                raw=self.unpack_chunk(row[2])
                if hashlib.sha256(raw).hexdigest()!=row[1]:raise ValueError('Native replay checksum changed')
                value=json.loads(raw)
                if value.get('sequence')!=expected or value.get('end')!=(expected==total-1):raise ValueError('Native replay chunk boundary changed')
                start=len(validator.output);validator.append(value['ops']);clean=validator.output[start:]
                for operation in clean:
                    count+=1
                    if count>native.MAX_OPERATIONS:raise ValueError('Native replay is too long')
                    if count in boundaries:
                        if operation!=['loop',1]:raise ValueError('Invalid portable engine boundary')
                        boundaries.remove(count)
                # The validator needs only the last row plus its scalar stack
                # state between chunks; keep the full tape only in its JS owner.
                validator.output[:]=validator.output[-1:]
                if expected==total-1:
                    validator.finish()
                    if boundaries:raise ValueError('Portable engine boundary exceeds recording')
                yield dict(sequence=expected,ops=clean,end=expected==total-1,total=total)

    def statuses(self,identifiers,adapter,client=None):
        result=super().statuses(identifiers,adapter)
        if client is None:
            for row in result.values():row.update(available=False,reason='client-unverified')
            return result
        with self.connect()as db:
            import json
            for identifier,row in result.items():
                header=json.loads(db.execute('SELECT header FROM attempts WHERE id=?',(identifier,)).fetchone()[0])
                if header.get('client')!=client:row.update(available=False,reason='incompatible')
                elif header.get('schema')==self.schema and compatible_adapter(header.get('adapter'),adapter):
                    complete=row['reason']!='recording'
                    row.update(available=complete,reason='ready' if complete else 'recording')
        return result

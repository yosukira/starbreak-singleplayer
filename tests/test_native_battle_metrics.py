import ast
import hashlib
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest import mock

from plugins import _native_battle_metrics as metrics


class NativeHealthMetricTests(unittest.TestCase):
    def environment(self):
        props=NS(hitpoints=150)
        player=NS(asClientObject=NS(asNativeObject=NS(asWorldObject=NS(props=props))))
        world=NS(asWorld=NS(props=NS(seed=77)),player=player)
        refs=NS(WorldClient=NS(clientWorld=world))
        ffi=mock.Mock(NULL=None);ffi.sizeof.return_value=4
        def cast(kind,value):
            if kind=='uintptr_t':return 100
            if kind=='void *':return 200 if value is player else value
            return value
        ffi.cast.side_effect=cast;body=b'checked test code'*9
        ffi.buffer.return_value=body
        hook=mock.Mock(error=None)
        factory=mock.Mock(return_value=hook)
        with mock.patch.object(metrics,'BODY',hashlib.sha256(body).hexdigest()):
            observer=metrics.NativeHealthMetrics(ffi,None,refs,0x400000,hook=factory)
        return observer,props,world,hook,factory,ffi

    def test_original_health_result_is_observed_once_and_ignores_other_characters(self):
        observer,props,world,hook,factory,_=self.environment()
        self.assertEqual(factory.call_args.kwargs['arguments'],('int','unsigned int','unsigned int'))
        self.assertEqual(factory.call_args.kwargs['convention'],'thiscall');hook.install.assert_called_once()
        def hurt(pointer,amount,add,force):props.hitpoints-=17;return 99
        original=mock.Mock(side_effect=hurt)
        self.assertEqual(observer.observe(original,200,-100,True,False),99)
        original.assert_called_once_with(200,-100,True,False)
        self.assertEqual(observer.document()['damage_taken'],17)
        props.hitpoints=150
        observer.observe(hurt,300,-100,True,False)
        self.assertEqual(observer.document()['damage_taken'],17,'Other characters cannot affect local stats')
        hook.error='ValueError';self.assertEqual(observer.document(),{},'An observer failure must not claim a complete zero')

    def test_capped_damage_healing_initialization_and_nested_adjustments(self):
        totals=metrics.HealthTotals();totals.reset('room1')
        entry=totals.before(0);totals.after(entry,150)
        self.assertEqual(totals.healing,0,'Spawn health is not healing')
        entry=totals.before(150);totals.after(entry,133)
        entry=totals.before(133);totals.after(entry,150)
        entry=totals.before(150);totals.after(entry,150)
        self.assertEqual(totals.document(),dict(protocol='player-metrics-v1',damage_taken=17,healing=17))
        # Nested healing cannot mask the outer 20-point hit or count twice.
        outer=totals.before(100);inner=totals.before(100)
        totals.after(inner,110);totals.after(outer,90)
        self.assertEqual(totals.damage_taken,37);self.assertEqual(totals.healing,27)
        entry=totals.before(90);totals.after(entry,-999)
        self.assertEqual(totals.damage_taken,127,'Fatal overkill is capped to the remaining health')
        totals.reset('room2');self.assertEqual(totals.damage_taken,0)

    def test_native_world_replacement_resets_cumulative_counters(self):
        observer,props,world,*_=self.environment()
        observer.observe(lambda *args:setattr(props,'hitpoints',100),200,-50,True,False)
        self.assertEqual(observer.document()['damage_taken'],50)
        world.asWorld.props.seed+=1;props.hitpoints=150
        self.assertEqual(observer.document()['damage_taken'],0)

    def test_offline_and_exact_body_checks_precede_any_native_hook(self):
        with mock.patch.dict('os.environ',{'HARDLAB_LIVE_SESSION':'0'}):self.assertIsNone(metrics.create(None,None,None))
        ffi=mock.Mock(NULL=None);ffi.sizeof.return_value=4;ffi.buffer.return_value=b'wrong client'
        hook=mock.Mock()
        with self.assertRaisesRegex(ValueError,'differs'):metrics.NativeHealthMetrics(ffi,None,None,0x400000,hook=hook)
        hook.assert_not_called()
        ast.parse(Path('plugins/_native_battle_metrics.py').read_text(),feature_version=(3,5))


if __name__=='__main__':unittest.main()

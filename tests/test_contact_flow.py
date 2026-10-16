from contact_fixtures import apply_enabled_snapshot
import json
from pathlib import Path
import pytest
from galchat.contact_database import ContactDatabase, contact_key, resolve_contact
from galchat.contact_preferences import ContactPreferences
from galchat.scenes import default_scene_id
from galchat.settings import Settings
from galchat.controller import Controller
from galchat.window_group import placement
from galchat.wechat import WeChatError
from galchat.current_chat import CurrentChatAdapter
from galchat.engine import ReplyEngine
from galchat.contact_profile import contact_gender


def test_same_name_never_uses_first_candidate():
    with pytest.raises(WeChatError, match='重名'):
        resolve_contact('小林',[{'username':'one','nick_name':'小林'}, {'username':'two','remark':'小林'}])
    assert resolve_contact('小林',[{'username':'one','nick_name':'小林','remark':'同事林'},
                                    {'username':'two','remark':'小林'}])['username']=='two'


def test_role_is_stable_after_remark_change_and_separate_for_accounts(tmp_path):
    prefs=ContactPreferences(tmp_path)
    key=contact_key('account','person')
    prefs.save(key,{'role':'child-female'})
    assert ContactPreferences(tmp_path).get(contact_key('account','person'))['role']=='child-female'
    assert contact_key('other-account','person') != key
    assert prefs.get(key)['adult_context'] is False


def test_scoped_recent_count_and_sender_direction(tmp_path):
    class Reader:
        wxid='self'
        def search_contact(self,name):return [{'username':'other','nick_name':name}]
        def get_messages(self,username,limit,offset):
            assert username=='other' and limit in {100,137,1680} and offset==0
            return [{'local_id':1,'sort_seq':2,'type':'文本','local_type':1,'content':'回复','sender_username':'self','_galchat_shard':'b'},
                    {'local_id':1,'sort_seq':1,'type':'文本','local_type':1,'content':'问题','sender_username':'other','_galchat_shard':'a'}]
    db=ContactDatabase(Path('.'),tmp_path,Reader())
    rows=db.recent(db.resolve('小林'),100)
    assert [m['role'] for m in rows]==['other','self']
    assert [m['kind'] for m in rows]==['text','text']
    assert len({m['id'] for m in rows})==2
    for count in (137,1680):assert len(db.recent(db.resolve('小林'),count))==2
    with pytest.raises(ValueError):db.recent(db.resolve('小林'),5001)


def test_maximized_modes_and_no_left_fallback():
    assert placement((0,0,1000,800),(0,0,1000,800),(400,600),True,'hide') is None
    assert placement((0,0,1000,800),(0,0,1000,800),(400,600),True,'overlay')==(588,70,True)
    assert placement((10,10,500,700),(0,0,1000,800),(400,600),False)==(508,10,False)
    assert placement((600,10,980,700),(0,0,1000,800),(400,600),False) is None


def test_settings_validate_count_and_modes(tmp_path):
    settings=Settings(tmp_path)
    assert settings.public()['history_limit']==100
    settings.save({'history_limit':10,'emoji_mode':'off','maximized_mode':'overlay','overlay_opacity':20})
    assert Settings(tmp_path).public()['history_limit']==10
    for bad in ({'history_limit':True},{'history_limit':9},{'emoji_mode':'lots'},{'overlay_opacity':0}):
        with pytest.raises(ValueError):settings.save(bad)


def test_choice_click_revalidates_identity_and_keeps_existing_draft(tmp_path):
    app=Controller(Path(__file__).resolve().parents[1],tmp_path,start_worker=False)
    app._demo(True)
    state=app.get_state()
    choice=state['choices'][0]
    with pytest.raises(ValueError,match='更新'):
        app._handle_native('choose_fill',{'id':choice['id'],'contact_id':'wrong','revision':state['revision']})
    result=app._handle_native('choose_fill',{'id':choice['id'],'contact_id':state['contact']['id'],'revision':state['revision']})
    assert '未发送' in result['message']
    app.state['mode']='wechat'
    app.contact_preferences.save(app.state['contact']['id'], {'assistance_enabled': True})
    class Adapter:
        def fill_draft(self,*args):return {'ok':False,'message':'微信已有草稿，已保留原文。'}
    app.adapter=Adapter()
    with pytest.raises(ValueError,match='已有草稿'):
        app._handle_native('choose_fill',{'id':choice['id'],'contact_id':state['contact']['id'],'revision':state['revision']})
    app.close()


def test_recent_window_not_old_memory_controls_count(tmp_path):
    app=Controller(Path(__file__).resolve().parents[1],tmp_path,start_worker=False)
    app.settings.save({'history_limit':10})
    rows=[{'id':str(i),'role':'other','text':str(i),'timestamp':None,'source':'fixture'} for i in range(100)]
    apply_enabled_snapshot(app, {'contact':{'id':'A','name':'A'},'messages':rows})
    assert len(app.get_state()['messages'])==10
    app.close()


def test_read_discarded_when_chat_switches_mid_query():
    class Database:
        def resolve(self,name):return {'id':name,'name':name}
        def recent(self,*args):return []
    adapter=CurrentChatAdapter(Database(),None,lambda:{'history_limit':100})
    sequence=iter([{'name':'A','window':{'fingerprint':'window'}},
                   {'name':'B','window':{'fingerprint':'window'}}])
    adapter.current=lambda **kwargs:next(sequence)
    with pytest.raises(WeChatError,match='切换'):adapter.snapshot()
    assert adapter._last is None


@pytest.mark.parametrize('role,kind,blocked',[
    ('other','emoji',False),('self','emoji',False),('unknown','emoji',True),
    ('other','image',True),('other','voice',True)])
def test_snapshot_allows_known_emoji_but_not_unreadable_media(role,kind,blocked):
    class Database:
        def resolve(self,name):return {'id':name,'name':name}
        def recent(self,*args):return [{'role':role,'kind':kind,'text':'[表情]'}]
    adapter=CurrentChatAdapter(Database(),None,lambda:{'history_limit':100})
    adapter.current=lambda **kwargs:{'name':'A','mode':'uia','window':{'fingerprint':'window','rect':[]}}
    assert bool(adapter.snapshot()['generation_blocked_reason']) is blocked


def test_unmatched_cached_title_gets_one_fresh_exact_lookup():
    from types import SimpleNamespace
    calls=[]
    class Database:
        def resolve(self,name):
            if name!='于总':raise WeChatError('contact_not_found','未匹配')
            return {'id':'verified','name':name}
        def recent(self,*args):return []
    adapter=CurrentChatAdapter(Database(),SimpleNamespace(),lambda:{'history_limit':100})
    def current(force=False):
        calls.append(force)
        return {'name':'干总' if len(calls)==1 else '于总','mode':'visual',
                'signature':('window','same-title-pixels'),'window':{'fingerprint':'window','rect':[]}}
    adapter.current=current
    assert adapter.snapshot()['contact']['id']=='verified'
    assert calls==[False,True,False]


def test_unmatched_title_does_not_loop_or_pick_fuzzy_candidate():
    from types import SimpleNamespace
    calls=[]
    class Database:
        def resolve(self,name):raise WeChatError('contact_not_found','未匹配')
    adapter=CurrentChatAdapter(Database(),SimpleNamespace(),lambda:{'history_limit':100})
    def current(force=False):
        calls.append(force)
        return {'name':'未匹配','mode':'visual','signature':('window','title'),
                'window':{'fingerprint':'window'}}
    adapter.current=current
    for _ in range(3):
        with pytest.raises(WeChatError):adapter.snapshot()
    assert calls==[False,True,False,False]
    assert adapter._last is None


@pytest.mark.parametrize('changed',[True,False])
def test_title_failure_cooldown_does_not_block_a_different_chat(changed):
    from types import SimpleNamespace
    from PIL import Image
    from galchat.vision import VisionReaderError
    images=[Image.new('RGB',(10,10),color) for color in ('white','black')]
    calls=[]
    frame=[0]
    class Reader:
        def read_title(self,*args):
            calls.append(True)
            if len(calls)==1:raise VisionReaderError('看不清')
            return {'contact_name':'新聊天'}
    visual=SimpleNamespace(reader=Reader(),settings_provider=lambda:{},
                          backend=SimpleNamespace(window=lambda:{'hwnd':1}))
    visual._capture=lambda:({'fingerprint':'same-window'},images[frame[0]])
    visual._part=lambda image,key:image
    adapter=CurrentChatAdapter(None,visual,lambda:{})
    adapter._uia_current=lambda window:None
    with pytest.raises(VisionReaderError):adapter.current()
    if changed:
        frame[0]=1
        assert adapter.current()['name']=='新聊天'
        assert len(calls)==2
    else:
        with pytest.raises(WeChatError) as error:adapter.current()
        assert error.value.code=='vision_cooldown'
        assert len(calls)==1


def test_fill_stops_if_chat_changes_during_focus():
    class Pattern:
        Value=''
        writes=[]
        def SetValue(self,value):self.writes.append(value)
    class Edit:
        def GetValuePattern(self):return pattern
    class Backend:
        def focus(self,window):pass
    class Visual:
        backend=Backend()
    class Database:
        def resolve(self,name):return {'id':name}
        def recent(self,*args):return []
    pattern=Pattern()
    adapter=CurrentChatAdapter(Database(),Visual(),lambda:{'history_limit':100})
    adapter._last={'contact':{'id':'A'},'messages':[]}
    sequence=iter([{'name':'A','mode':'uia','window':{},'edit':Edit()},
                   {'name':'B','mode':'uia','window':{},'edit':Edit()}])
    adapter.current=lambda **kwargs:next(sequence)
    result=adapter.fill_draft('A','测试草稿')
    assert result['ok'] is False and '切换' in result['message']
    assert pattern.writes==[]


def test_model_receives_selected_hundred_rows_and_preferences():
    rows=[{'role':'self' if i%2 else 'other','text':f'第{i}条','contact_id':'A'} for i in range(100)]
    prefs={'emoji_mode':'off','tone_balance':90,'adult_context':False}
    context=ReplyEngine._payload_context({'contact_id':'A','recent':rows,'reply_preferences':prefs})
    assert len(context['recent'])==100
    assert context['recent'][0]['text']=='第0条' and context['recent'][-1]['text']=='第99条'
    assert context['reply_preferences']==prefs


def test_display_change_does_not_invalidate_or_regenerate_replies(tmp_path):
    app=Controller(Path(__file__).resolve().parents[1],tmp_path,start_worker=False)
    app._demo(True)
    before=app.get_state()
    result=app.dispatch('save_settings',{'font_size':20,'maximized_mode':'overlay'})
    assert result['ok']
    assert result['state']['revision']==before['revision']
    assert result['state']['choices']==before['choices']
    app.close()


def test_open_wechat_restores_existing_window_without_launch(monkeypatch):
    from galchat.visual_wechat import VisualWeChatAdapter
    class GUI:
        calls=[]
        def ShowWindow(self,hwnd,command):self.calls.append((hwnd,command))
    class Backend:
        gui=GUI()
        focused=[]
        def window(self,**kwargs):return {'hwnd':123}
        def focus(self,window):self.focused.append(window['hwnd'])
    backend=Backend()
    adapter=VisualWeChatAdapter(lambda:{},backend=backend)
    monkeypatch.setattr('subprocess.Popen',lambda *a,**k:pytest.fail('must restore existing window'))
    assert adapter.open_wechat()['ok']
    assert backend.gui.calls==[(123,9)] and backend.focused==[123]


def test_gender_buffer_skips_unrelated_fields_and_rejects_corruption():
    assert contact_gender(b'\x22\x03abc\x10\x01')==1
    assert contact_gender(b'\x10\x02\x28\x01')==2
    assert contact_gender(b'\x10\x00')==0
    assert contact_gender(b'\x10\x02\x22\xff')==0
    assert contact_gender(b'\x10\x01\x10\x02')==0
    assert contact_gender(b'\x10\x03')==0
    assert contact_gender(None)==0


def test_known_gender_default_and_manual_role_precedence(tmp_path):
    prefs=ContactPreferences(tmp_path)
    assert prefs.get('A',{'gender':2})['role']=='young-female'
    assert prefs.get('B',{'gender':1})['role']=='young-male'
    assert prefs.get('unknown',{'gender':0})['role_source']=='default'
    assert prefs.get('unknown',{'gender':0})['role']=='unknown-female'
    prefs.save('A',{'role':'senior-male'})
    assert prefs.get('A',{'gender':2})['role']=='senior-male'
    assert prefs.get('A',{'gender':2})['role_source']=='manual'
    prefs.save('female',{'adult_context':True},{'gender':2})
    assert prefs.get('female',{'gender':2})['role']=='young-female'


def test_appearance_reset_keeps_relationship_preferences_and_other_contacts(tmp_path):
    prefs=ContactPreferences(tmp_path)
    profile={'gender':2}
    prefs.save('A',{'role':'senior-male','scene_id':'park','adult_context':True,
                    'pursuit_goal':'express_interest','pursuit_stage':'talking'},profile)
    prefs.save('B',{'role':'middle-female'})
    before=prefs.get('A',profile)
    result=prefs.save('A',{'reset_appearance':True},profile)
    assert result['role']=='young-female' and result['role_source']=='contact_gender'
    assert result['scene_id']==default_scene_id('A')
    for key in ('adult_context','pursuit_goal','pursuit_stage'):
        assert result[key]==before[key]
    assert prefs.get('B')['role']=='middle-female'


def test_saved_automatic_role_refreshes_with_gender_and_new_default(tmp_path):
    prefs=ContactPreferences(tmp_path)
    prefs.values['A']={'role':'urban-male','role_source':'contact_gender','adult_context':True}
    actual=prefs.get('A',{'gender':2})
    assert actual=={'role':'young-female','role_source':'contact_gender','adult_context':True,'assistance_enabled':False,'assistance_decided':False,
                    'pursuit_goal':'get_to_know','pursuit_stage':'unknown','scene_id':default_scene_id('A')}
    prefs.save('A',{'adult_context':False},{'gender':2})
    loaded=ContactPreferences(tmp_path)
    assert loaded.get('A',{'gender':1})['role']=='young-male'
    assert loaded.get('A',{'gender':0})['role']=='unknown-female'


def test_legacy_selected_role_remains_manual(tmp_path):
    prefs=ContactPreferences(tmp_path)
    prefs.values['A']={'role':'older-female','adult_context':False}
    assert prefs.get('A',{'gender':1})['role']=='older-female'
    assert prefs.get('A',{'gender':1})['role_source']=='manual'


@pytest.mark.parametrize('value,expected_role',[(1,'young-male'),(2,'young-female'),(0,'unknown-female')])
def test_database_gender_flows_to_default_role(tmp_path,value,expected_role):
    import sqlite3
    path=tmp_path/'contact.db'
    conn=sqlite3.connect(path)
    conn.execute('CREATE TABLE contact(username,nick_name,remark,extra_buffer)')
    conn.execute('INSERT INTO contact VALUES(?,?,?,?)',('other','昵称','备注',bytes([16,value])))
    conn.commit()
    conn.close()
    class Reader:
        wxid='account'
        def _contact_conn(self):
            result=sqlite3.connect(path)
            result.row_factory=sqlite3.Row
            return result
    contact=ContactDatabase(Path('.'),tmp_path,Reader()).resolve('备注')
    assert contact['gender']==value
    assert ContactPreferences(tmp_path).get(contact['id'],contact)['role']==expected_role


def test_account_reads_release_windows_snapshot_file(tmp_path):
    import sqlite3
    from galchat.contact_database import read_self_info
    snapshot=tmp_path/'profile.db'
    with sqlite3.connect(snapshot) as seed:
        seed.execute('CREATE TABLE contact(username,nick_name,remark)')
        seed.execute("INSERT INTO contact VALUES('self','自己','')")
    seed.close()
    class Reader:
        wxid='self'
        def _contact_conn(self):
            connection=sqlite3.connect(f'file:{snapshot}?mode=ro',uri=True)
            connection.row_factory=sqlite3.Row
            return connection
    reader=Reader()
    for index in range(10):
        assert read_self_info(reader)['nick_name']=='自己'
        replacement=tmp_path/'replacement.db'
        replacement.write_bytes(snapshot.read_bytes())
        replacement.replace(snapshot)  # Fails on Windows if a SQLite handle leaks.


def test_native_group_raises_pair_and_preserves_minimization(tmp_path,monkeypatch):
    from galchat.window_group import WindowGroup
    from types import SimpleNamespace
    import ctypes
    class Function:
        def __init__(self,value):self.value=value
        def __call__(self,*args):return self.value
    monkeypatch.setattr(ctypes.windll.user32,'IsZoomed',Function(0))
    monkeypatch.setattr(ctypes.windll.user32,'GetDpiForWindow',Function(96))
    class GUI:
        fg=1
        iconic=False
        positions=[]
        insert_after=[]
        show_commands=[]
        rects={1:(100,100,900,800),2:(500,100,900,700)}
        def GetForegroundWindow(self):return self.fg
        def GetAncestor(self,h,level):return h
        def GetWindowRect(self,h):return self.rects[h]
        def IsIconic(self,h):return self.iconic if h==2 else False
        def IsWindow(self,h):return True
        def IsWindowVisible(self,h):return True
        def ShowWindow(self,h,command):
            self.show_commands.append(command)
            self.iconic=command==6
        def GetWindowLong(self,*args):return 0
        def SetWindowLong(self,*args):pass
        def SetLayeredWindowAttributes(self,*args):pass
        def SetWindowPos(self,h,after,x,y,w,height,flags):
            self.positions.append((h,flags))
            self.insert_after.append(after)
            if h==2:self.rects[h]=(x,y,x+400,y+600)
    gui=GUI()
    class API:
        def MonitorFromRect(self,*args):return 1
        def GetMonitorInfo(self,*args):return {'Work':(0,0,2000,1000)}
    class Backend:
        api=API()
        con=SimpleNamespace(MONITOR_DEFAULTTONEAREST=2)
        def window(self):return {'hwnd':1,'rect':[100,100,900,800]}
    backend=Backend();backend.gui=gui
    controller=SimpleNamespace(settings=SimpleNamespace(directory=tmp_path,values={'maximized_mode':'hide','overlay_opacity':22}))
    group=WindowGroup(controller,backend);group._hwnd=lambda:2
    group.target=1
    group.user_opened=False  # Normal following, after the explicit launch phase.
    restored=[]
    group._restore_content=lambda:restored.append(True)
    group._frame()
    assert len(restored)==1
    assert gui.rects[2]==(908,100,1308,700)
    assert gui.positions[0]==(1,0x0001|0x0002|0x0010)
    before=len(gui.positions)
    group._frame()
    assert len(gui.positions)==before
    assert len(restored)==1
    gui.rects[1]=(120,100,920,800)
    group._frame()
    assert gui.rects[2]==(928,100,1328,700)
    assert len(gui.positions)==before+1 and gui.positions[-1]==(2,0x0001|0x0004|0x0010)
    with group.capture_guard():
        group._frame()
        assert group.visible and group.diagnostic['status']=='attached'
    before=len(gui.positions)
    gui.fg=999;group._frame()
    assert group.visible and group.diagnostic['status']=='background_paused'
    assert len(gui.positions)==before and 0 not in gui.show_commands
    group.visible=False  # Do not restore either background window.
    group._frame()
    assert not group.visible and len(gui.positions)==before
    assert len(restored)==1
    gui.fg=1;group._frame()
    group.minimize();assert gui.iconic
    group._frame();assert gui.iconic and group.diagnostic['status']=='minimized'
    gui.fg=999;group._frame();assert gui.iconic
    gui.fg=1;group._frame();assert not gui.iconic and group.diagnostic['status']=='attached'


def test_group_hides_managed_webview_before_form_not_just_hwnd(monkeypatch):
    import sys
    from types import SimpleNamespace
    from galchat.window_group import WindowGroup
    calls = []
    class View:
        def __setattr__(self, key, value):
            if key == 'Visible': calls.append(('view', value))
            super().__setattr__(key, value)
    class Form:
        webview = View()
        def Invoke(self, callback): callback()
        def Hide(self): calls.append(('form', False))
    monkeypatch.setitem(sys.modules, 'System', SimpleNamespace(Action=lambda callback:callback))
    group = WindowGroup(SimpleNamespace(window=SimpleNamespace(native=Form())), SimpleNamespace())
    group.is_active = lambda: True
    group._hide_content()
    assert calls == [('view', False), ('form', False)]

from copy import deepcopy
from types import SimpleNamespace as NS
import pytest
from galchat.native_voice_control import align_voice,begin_conversion,VOICE,TEXT
from galchat.wechat import WeChatError


class Node:
    def __init__(self,cls,name='',aid='',rect=(0,0,400,100),children=None,runtime=1):
        self.ClassName,self.Name,self.AutomationId=cls,name,aid
        self.BoundingRectangle=NS(left=rect[0],top=rect[1],right=rect[2],bottom=rect[3])
        self.IsOffscreen=False;self.IsEnabled=True;self.ProcessId=1;self.NativeWindowHandle=100
        self.children=children or [];self.runtime=runtime
    def GetChildren(self):return self.children
    def GetRuntimeId(self):return [self.runtime]


def records():return [
    {'id':'a','kind':'voice','role':'self','duration_seconds':3.712,'timestamp':'2026-10-13T20:39:35'},
    {'id':'b','kind':'voice','role':'self','duration_seconds':7.333,'timestamp':'2026-10-13T20:39:37'},
    {'id':'c','kind':'text','role':'other','text':'这个安排可以','timestamp':'2026-10-13T20:42:00'}]


def nodes():return [Node(VOICE,'语音4"秒',rect=(0,0,400,50),runtime=1),
    Node(VOICE,'语音7"秒',rect=(0,50,400,100),runtime=2),
    Node(TEXT,'这个安排可以',rect=(0,100,400,150),runtime=3)]


def test_exact_anchor_and_unique_chronology_bind_target_not_bottom_row():
    view=nodes()
    assert align_voice(view,records(),'b') is view[1]
    assert align_voice(view,records(),'a') is view[0]


def test_durations_alone_and_repeated_sequences_do_not_authorize_conversion():
    with pytest.raises(WeChatError):align_voice(nodes()[:2],records(),'b')
    with pytest.raises(WeChatError):align_voice(nodes(),records()+records(),'b')
    value=records();value[2]['text']='另一个安排'
    with pytest.raises(WeChatError):align_voice(nodes(),value,'b')


def test_adjacent_completed_voice_requires_exact_native_transcript_not_just_duration():
    view=nodes();data=records()
    data[1].update(text='这是已转换的内容',content_known=True,content_source='wechat_transcription')
    view[1].Name+='这是已转换的内容'
    assert align_voice(view,data,'a') is view[0]
    view[1].Name='语音7"秒另一个人的内容'
    with pytest.raises(WeChatError):align_voice(view,data,'a')


def fixture():
    data=records();view=nodes()
    lst=Node('mmui::RecyclerListView',aid='chat_message_list',rect=(0,0,400,200),children=view)
    root=Node('mmui::MainWindow',children=[lst])
    value=NS(Value='',IsReadOnly=False)
    edit=NS(GetValuePattern=lambda:value)
    count={'right':0,'invoke':0,'click':0,'focus':0}
    item=Node('fixture::MenuItem','语音转文字',rect=(280,70,400,110),runtime=44)
    item.GetPattern=lambda pid:NS(Invoke=lambda:count.__setitem__('invoke',count['invoke']+1))
    item.Click=lambda **kw:count.__setitem__('click',count['click']+1)
    def right(**kwargs):
        assert .6<kwargs['ratioX']<.85
        count['right']+=1;root.children.append(item)
    view[1].RightClick=right
    backend=NS(api=NS(GetAsyncKeyState=lambda k:0),
        focus=lambda window:count.__setitem__('focus',count['focus']+1),_point=lambda *args:None,
        gui=NS(GetForegroundWindow=lambda:100,GetWindow=lambda *args:100),con=NS(GW_OWNER=4),
        _exe=lambda hwnd:(1,'C:/fixture/Weixin.exe'))
    auto=NS(ControlFromHandle=lambda hwnd:root,GetRootControl=lambda:Node('',children=[]),
        PatternId=NS(InvokePattern=10000),ControlFromPoint=lambda *args:item)
    adapter=NS(auto=auto,database=NS(resolve=lambda name:{'id':'A'},recent=lambda *args:deepcopy(data)),
        visual=NS(backend=backend),settings_provider=lambda:{'history_limit':100},
        current=lambda **kwargs:{'name':'person','edit':edit,'window':{'hwnd':100,'pid':1,'fingerprint':'same','rect':[0,0,400,500]}})
    return adapter,root,view,item,value,count,data


def test_verified_menu_invoke_uses_native_controls_without_ocr_or_asr():
    adapter,_,_,_,value,count,_=fixture()
    result=begin_conversion(adapter,'A','b')
    assert result['status']=='processing' and result['method']=='uia_invoke'
    assert count=={'right':1,'invoke':1,'click':0,'focus':1}
    assert value.Value==''


def test_native_click_fallback_checks_exact_control_under_pointer():
    adapter,_,_,item,_,count,_=fixture()
    item.GetPattern=lambda pid:None
    assert begin_conversion(adapter,'A','b')['method']=='uia_menu_click'
    assert count['click']==1 and count['invoke']==0


def test_obscured_menu_does_not_click_another_window():
    adapter,_,_,item,_,count,_=fixture()
    item.GetPattern=lambda pid:None
    other=Node('foreign',runtime=55);other.GetParentControl=lambda:None
    adapter.auto.ControlFromPoint=lambda *args:other
    with pytest.raises(WeChatError,match='遮挡'):begin_conversion(adapter,'A','b')
    assert count['invoke']==count['click']==0


def test_draft_changed_while_menu_open_is_preserved():
    adapter,_,view,_,value,count,_=fixture()
    original=view[1].RightClick
    def right(**kw):original(**kw);value.Value='手写草稿'
    view[1].RightClick=right
    with pytest.raises(WeChatError,match='编辑'):begin_conversion(adapter,'A','b')
    assert count['invoke']==count['click']==0 and value.Value=='手写草稿'


def test_contact_switch_and_preexisting_menu_stop_before_input():
    adapter,root,_,item,_,count,_=fixture()
    with pytest.raises(WeChatError,match='切换'):begin_conversion(adapter,'B','b')
    root.children.append(item)
    with pytest.raises(WeChatError,match='已有菜单'):begin_conversion(adapter,'A','b')
    assert count['right']==count['invoke']==0


def test_recycled_row_with_same_duration_and_text_is_not_the_bound_message():
    adapter,_,view,_,_,count,_=fixture()
    original=view[1].RightClick
    def right(**kw):original(**kw);view[1].runtime=999
    view[1].RightClick=right
    with pytest.raises(WeChatError,match='气泡已经变化'):begin_conversion(adapter,'A','b')
    assert count['invoke']==count['click']==0


def test_changed_sender_or_time_stops_before_opening_menu():
    adapter,_,_,_,_,count,data=fixture()
    original=deepcopy(data[1]);original['role']='other'
    with pytest.raises(WeChatError,match='记录已经变化'):
        begin_conversion(adapter,'A','b',expected_message=original)
    assert count['right']==count['invoke']==count['click']==0


def test_foreground_switch_after_right_click_stops_even_with_invoke_support():
    adapter,_,view,_,_,count,_=fixture()
    original=view[1].RightClick
    def right(**kw):
        original(**kw)
        adapter.visual.backend.gui.GetForegroundWindow=lambda:7
        adapter.visual.backend._exe=lambda hwnd:(2,'C:/fixture/other.exe')
    view[1].RightClick=right
    with pytest.raises(WeChatError,match='焦点已离开'):begin_conversion(adapter,'A','b')
    assert count['invoke']==count['click']==0


def test_wechat_owned_menu_may_hold_focus_during_invoke():
    adapter,_,view,_,_,count,_=fixture()
    original=view[1].RightClick
    def right(**kw):
        original(**kw)
        adapter.visual.backend.gui.GetForegroundWindow=lambda:44
    view[1].RightClick=right
    assert begin_conversion(adapter,'A','b')['method']=='uia_invoke'
    assert count['invoke']==1


def test_one_native_focus_request_is_verified_before_input():
    adapter,_,_,_,_,count,_=fixture()
    backend=adapter.visual.backend
    backend.focus=lambda window:(_ for _ in ()).throw(WeChatError('focus_failed','前台未确认'))
    focused=[]
    backend.gui.GetForegroundWindow=lambda:100 if focused else 7
    edit=adapter.current()['edit']
    edit.SetFocus=lambda:focused.append('native')
    assert begin_conversion(adapter,'A','b')['method']=='uia_invoke'
    assert focused==['native'] and count['right']==1


def test_native_focus_cannot_bypass_a_foreground_rejection():
    adapter,_,_,_,_,count,_=fixture()
    backend=adapter.visual.backend
    backend.focus=lambda window:(_ for _ in ()).throw(WeChatError('focus_failed','前台未确认'))
    backend.gui.GetForegroundWindow=lambda:7
    adapter.current()['edit'].SetFocus=lambda:None
    with pytest.raises(WeChatError,match='前台未确认'):begin_conversion(adapter,'A','b')
    assert count['right']==count['invoke']==count['click']==0


def test_clipped_text_anchor_does_not_block_a_fully_visible_voice():
    adapter,root,_,_,_,count,_=fixture()
    root.children[0].BoundingRectangle.bottom=140  # The last text row ends at150.
    assert begin_conversion(adapter,'A','b')['method']=='uia_invoke'
    assert count['invoke']==1


def test_partially_clipped_voice_is_never_clicked_even_with_readable_name():
    adapter,root,_,_,_,count,_=fixture()
    root.children[0].BoundingRectangle.top=60  # Target voice spans50..100.
    with pytest.raises(WeChatError):begin_conversion(adapter,'A','b')
    assert count['right']==count['invoke']==count['click']==0


def test_scrolling_target_outside_viewport_after_right_click_stops_conversion():
    adapter,root,view,_,_,count,_=fixture()
    original=view[1].RightClick
    def right(**kw):
        original(**kw)
        root.children[0].BoundingRectangle.top=60
    view[1].RightClick=right
    with pytest.raises(WeChatError,match='变化|完整显示'):begin_conversion(adapter,'A','b')
    assert count['right']==1 and count['invoke']==count['click']==0


class ProviderError(Exception):
    hresult=-2147220991


def test_known_provider_invoke_error_allows_only_rechecked_owned_menu_click():
    adapter,_,_,item,_,count,_=fixture()
    def fail():raise ProviderError('provider event failed')
    item.GetPattern=lambda pid:NS(Invoke=fail)
    assert begin_conversion(adapter,'A','b')['method']=='uia_menu_click_after_invoke_error'
    assert count['right']==count['click']==1


def test_failed_invoke_with_disappeared_menu_is_not_clicked_again():
    adapter,root,_,item,_,count,_=fixture()
    def gone():root.children.remove(item);raise ProviderError('unknown outcome')
    item.GetPattern=lambda pid:NS(Invoke=gone)
    with pytest.raises(ProviderError):begin_conversion(adapter,'A','b')
    assert count['click']==0


def test_unrecognised_invoke_error_does_not_fall_back_to_click():
    adapter,_,_,item,_,count,_=fixture()
    def fail():raise RuntimeError('different provider failure')
    item.GetPattern=lambda pid:NS(Invoke=fail)
    with pytest.raises(RuntimeError):begin_conversion(adapter,'A','b')
    assert count['click']==0


@pytest.mark.parametrize('cls',['mmui::XMenuView','mmui::XMenuItemView'])
def test_qt_menu_with_broken_invoke_uses_verified_native_click_directly(cls):
    adapter,_,_,item,_,count,_=fixture()
    item.ClassName=cls
    item.GetPattern=lambda pid:pytest.fail('Qt menu Invoke must not be requested')
    assert begin_conversion(adapter,'A','b')['method']=='uia_menu_click'
    assert count['right']==count['click']==1 and count['invoke']==0


def qt_popup_fixture():
    adapter,_,_,item,_,count,_=fixture()
    item.ClassName='mmui::XMenuView'
    popup=Node('mmui::XMenu',rect=(200,50,500,180),runtime=200)
    popup.NativeWindowHandle=200;popup.GetParentControl=lambda:None
    item.GetParentControl=lambda:popup
    adapter.auto.ControlFromPoint=lambda *args:popup
    adapter.visual.backend.gui.WindowFromPoint=lambda point:200
    return adapter,count


def test_qt_container_hit_is_accepted_only_with_native_owned_popup():
    adapter,count=qt_popup_fixture()
    assert begin_conversion(adapter,'A','b')['method']=='uia_menu_click'
    assert adapter.voice_diagnostic['hit_mode']=='owned_qt_menu_container'
    assert count['click']==1


@pytest.mark.parametrize('problem',['foreign_owner','native_other_window','foreign_process'])
def test_qt_container_hit_never_bypasses_other_window_or_account(problem):
    adapter,count=qt_popup_fixture()
    backend=adapter.visual.backend
    if problem=='foreign_owner':backend.gui.GetWindow=lambda *args:7
    if problem=='native_other_window':backend.gui.WindowFromPoint=lambda point:7
    if problem=='foreign_process':backend._exe=lambda hwnd:(2,'foreign.exe')
    with pytest.raises(WeChatError,match='遮挡'):begin_conversion(adapter,'A','b')
    assert count['click']==0


def test_automatic_conversion_never_requests_focus():
    adapter,_,_,_,_,count,_=fixture()
    adapter.visual.backend.focus=lambda window:pytest.fail('automatic action must not activate')
    assert begin_conversion(adapter,'A','b',automatic=True)['status']=='processing'
    assert count['right']==1 and count['focus']==0


@pytest.mark.parametrize('condition',['draft','foreground'])
def test_automatic_conversion_waits_for_empty_input_and_foreground(condition):
    adapter,_,_,_,value,count,_=fixture()
    if condition=='draft':value.Value='我正在写的内容'
    else:adapter.visual.backend.gui.GetForegroundWindow=lambda:7
    with pytest.raises(WeChatError) as error:begin_conversion(adapter,'A','b',automatic=True)
    assert error.value.code=='voice_deferred'
    assert count['right']==count['invoke']==count['click']==count['focus']==0

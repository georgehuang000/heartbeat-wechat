"""Explicit native WeChat voice conversion. No OCR, ASR service or send API.

The caller owns the UIA worker. Only a uniquely aligned visible message is
eligible; unrecognised/ambiguous views fail rather than selecting by duration.
"""
import math
import re
import time
from .wechat import WeChatError,_get,_walk,_rect,_value
from .activity import check_activity

VOICE='mmui::ChatVoiceItemView'
TEXT='mmui::ChatTextItemView'
MENU_NAMES={'语音转文字','转文字'}


def _matches(control,message):
    cls,name=_get(control,'ClassName'),str(_get(control,'Name')).strip()
    if cls==TEXT:
        return message.get('kind','text')=='text' and name==str(message.get('text','')).strip()
    if cls==VOICE and message.get('kind')=='voice':
        match=re.match(r'语音\s*(\d{1,3})["″”]?秒',name)
        duration=message.get('duration_seconds')
        if not (match and isinstance(duration,(float,int)) and math.isfinite(duration)
                and int(match[1])==math.floor(duration+0.5)):return False
        suffix=name[match.end():].strip()
        return not suffix or (message.get('content_source')=='wechat_transcription' and message.get('content_known')
            and suffix==str(message.get('text','')).strip())
    return False


def align_voice(visible,records,mid,*,return_binding=False):
    """Align a native visible suffix with DB chronology, including a text anchor.

    At least three rows, a real text anchor and a unique contiguous match are
    required. A duration or an arbitrary bottom-most voice is never enough.
    """
    candidates=[]
    for length in range(len(visible),2,-1):
        suffix=visible[-length:]
        if not any(_get(n,'ClassName')==TEXT and str(_get(n,'Name')).strip() for n in suffix):continue
        matches=[]
        for start in range(len(records)-length+1):
            batch=records[start:start+length]
            if all(_matches(n,m) for n,m in zip(suffix,batch)):
                matches.append((suffix,batch))
        if len(matches)!=1:continue
        nodes,batch=matches[0]
        hit=[n for n,m in zip(nodes,batch) if m.get('id')==mid and m.get('kind')=='voice']
        if len(hit)==1:candidates.append((hit[0],nodes));break
    if len(candidates)!=1:
        raise WeChatError('voice_not_bound','这条语音还没有唯一对应到当前可见气泡，请让它和前后消息一起出现在微信里。')
    return candidates[0] if return_binding else candidates[0][0]


def visible_rows(root):
    lists=[n for n in _walk(root) if _get(n,'AutomationId')=='chat_message_list']
    if len(lists)!=1:raise WeChatError('voice_list_missing','当前消息列表暂时不可读，请重试。')
    bounds=_rect(lists[0])
    if not bounds:raise WeChatError('voice_list_missing','当前消息列表暂时不可读，请重试。')
    rows=[]
    for node in lists[0].GetChildren():
        cls=str(_get(node,'ClassName'))
        if not cls.startswith('mmui::Chat') or cls in {'mmui::ChatItemView','mmui::ChatSystemInfoItemView'}:continue
        rect=_rect(node)
        # UIA names can still expose the complete text of clipped neighbours.
        # They are identity anchors only; the target itself must be fully visible.
        if (rect and not _get(node,'IsOffscreen',False) and rect[0]<bounds[2] and bounds[0]<rect[2]
                and rect[1]<bounds[3] and bounds[1]<rect[3]):
            rows.append(node)
    return rows


def require_visible_target(root,node):
    lists=[n for n in _walk(root) if _get(n,'AutomationId')=='chat_message_list']
    bounds=_rect(lists[0]) if len(lists)==1 else None
    rect=_rect(node)
    if (not bounds or not rect or _get(node,'IsOffscreen',False)
            or not (bounds[0]<=rect[0]<rect[2]<=bounds[2] and bounds[1]<=rect[1]<rect[3]<=bounds[3])):
        raise WeChatError('voice_not_visible','这条语音气泡没有完整显示，请把它移到微信聊天区内。')


def menu_items(auto,root,pid):
    nodes=list(_walk(root))
    # Enumerate only top-level siblings belonging to this WeChat process,
    # never recursively traverse the global Windows accessibility tree.
    for top in auto.GetRootControl().GetChildren():
        if _get(top,'ProcessId')==pid and _get(top,'NativeWindowHandle')!=_get(root,'NativeWindowHandle'):
            nodes.extend(_walk(top,depth=8,limit=150))
    unique={}
    for node in nodes:
        if ('Menu' in str(_get(node,'ClassName')) and str(_get(node,'Name')).strip() in MENU_NAMES
                and not _get(node,'IsOffscreen',False) and _get(node,'IsEnabled',True) and _rect(node)):
            unique[tuple(node.GetRuntimeId())]=node
    return list(unique.values())


def begin_conversion(adapter,cid,mid,*,expected_message=None,automatic=False):
    guard = lambda: check_activity(getattr(adapter, 'activity_check', None))
    guard()
    started=time.perf_counter()
    gui=adapter.current(require_native=True)
    contact=adapter.database.resolve(gui['name'])
    if contact['id']!=cid:raise WeChatError('chat_changed','聊天已经切换，这条语音未处理。')
    rows=adapter.database.recent(contact,adapter.settings_provider()['history_limit'])
    hits=[m for m in rows if m.get('id')==mid and m.get('kind')=='voice']
    if len(hits)!=1 or hits[0].get('role') not in {'self','other'}:
        raise WeChatError('stale_attachment','这条语音不属于当前已确认聊天。')
    message=hits[0]
    if expected_message is not None and any(message.get(k)!=expected_message.get(k)
            for k in ('id','role','kind','timestamp','duration_seconds','participant_id','conversation_kind')):
        raise WeChatError('stale_attachment','这条语音记录已经变化，请重新选择。')
    if message.get('content_source')=='wechat_transcription' and message.get('content_known'):
        return {'status':'ready','method':'stored_wechat_text','message':message}
    backend=adapter.visual.backend
    initial_value=_value(gui['edit'])[1]
    if initial_value is None:raise WeChatError('native_input_lost','暂时无法确认输入框状态，语音未处理。')
    window=gui['window']
    if automatic and (initial_value or backend.gui.GetForegroundWindow()!=window['hwnd']):
        raise WeChatError('voice_deferred','正在编辑或焦点不在微信，语音稍后读取。')
    root=adapter.auto.ControlFromHandle(window['hwnd'])
    if menu_items(adapter.auto,root,window['pid']):
        raise WeChatError('menu_already_open','微信已有菜单打开，请关闭后重试这条语音。')
    node,bound=align_voice(visible_rows(root),rows,mid,return_binding=True)
    require_visible_target(root,node)
    runtime=tuple(node.GetRuntimeId())
    binding=[(tuple(n.GetRuntimeId()),str(_get(n,'ClassName')),str(_get(n,'Name'))) for n in bound]

    def recheck():
        guard()
        fresh=adapter.current(require_native=True)
        if fresh['name']!=gui['name'] or fresh['window']['fingerprint']!=window['fingerprint'] or fresh['window']['rect']!=window['rect']:
            raise WeChatError('chat_changed','聊天已经切换，这条语音未继续处理。')
        if _value(fresh['edit'])[1]!=initial_value:
            raise WeChatError('draft_edited','输入框正在编辑，已停止语音操作并保留草稿。')
        foreground=backend.gui.GetForegroundWindow()
        if (foreground!=window['hwnd'] and (backend._exe(foreground)[0]!=window['pid']
                or backend.gui.GetWindow(foreground,backend.con.GW_OWNER)!=window['hwnd'])):
            raise WeChatError('focus_changed','焦点已离开微信，已停止语音操作。')
        if any(backend.api.GetAsyncKeyState(k)&0x8000 for k in (0x10,0x11,0x12,0x5b,0x5c)):
            raise WeChatError('modifier_pressed','请松开修饰键后重试这条语音。')
        current_root=adapter.auto.ControlFromHandle(window['hwnd'])
        current_rows=visible_rows(current_root)
        signatures=[(tuple(n.GetRuntimeId()),str(_get(n,'ClassName')),str(_get(n,'Name'))) for n in current_rows]
        matches=[i for i in range(len(signatures)-len(binding)+1) if signatures[i:i+len(binding)]==binding]
        targets=[n for n in current_rows if tuple(n.GetRuntimeId())==runtime]
        if len(matches)!=1 or len(targets)!=1:
            raise WeChatError('voice_changed','语音气泡已经变化，请重新选择。')
        require_visible_target(current_root,targets[0])
        return current_root,targets[0]

    if automatic:
        if backend.gui.GetForegroundWindow()!=window['hwnd']:
            raise WeChatError('voice_deferred','焦点已离开微信，语音稍后读取。')
    else:
        try:
            guard()
            backend.focus(window)
        except WeChatError as error:
            if error.code!='focus_failed':raise
            # One native accessibility focus request is a distinct supported
            # activation route. Verify the actual foreground before any input.
            try:gui['edit'].SetFocus()
            except Exception:raise error
            if backend.gui.GetForegroundWindow()!=window['hwnd']:raise error
    root,node=recheck()
    bounds=_rect(node)
    scale=backend.dpi_scale(window) if callable(getattr(backend,'dpi_scale',None)) else 1
    width=bounds[2]-bounds[0]
    inset=min(92*scale,width*.28)
    rx=1-inset/width if message['role']=='self' else inset/width
    point=(bounds[0]+int((bounds[2]-bounds[0])*rx),bounds[1]+int((bounds[3]-bounds[1])*.4))
    backend._point(window,(point[0]-window['rect'][0],point[1]-window['rect'][1]))
    guard()
    node.RightClick(ratioX=rx,ratioY=.4,simulateMove=False,waitTime=0)
    deadline=time.monotonic()+1.5
    selected=None
    while time.monotonic()<deadline:
        root,_=recheck()
        found=menu_items(adapter.auto,root,window['pid'])
        if len(found)>1:raise WeChatError('voice_menu_ambiguous','转文字菜单不唯一，本次未执行。')
        if found:selected=found[0];break
        time.sleep(.08)
    if selected is None:raise WeChatError('voice_menu_missing','微信没有提供这条语音的转文字菜单。')
    root,_=recheck()
    if tuple(selected.GetRuntimeId()) not in {tuple(n.GetRuntimeId()) for n in menu_items(adapter.auto,root,window['pid'])}:
        raise WeChatError('voice_menu_changed','转文字菜单已经变化，本次未执行。')
    # This locally tested Qt menu advertises Invoke but raises EVENT_E_ALL_
    # SUBSCRIBERS_FAILED and dismisses the menu without producing text. Use
    # its verified native click directly; compatible providers retain Invoke.
    qt_menu=str(_get(selected,'ClassName')).startswith('mmui::XMenu')
    pattern=None if qt_menu else selected.GetPattern(adapter.auto.PatternId.InvokePattern)
    adapter.voice_diagnostic={'menu_class':str(_get(selected,'ClassName')),
        'menu_type':str(_get(selected,'ControlTypeName')), 'qt_menu':qt_menu}
    invoke_error=None
    if pattern is not None:
        try:
            guard()
            pattern.Invoke()
            return {'status':'processing','method':'uia_invoke','message':message,
                'elapsed_ms':round((time.perf_counter()-started)*1000,2)}
        except Exception as error:
            # Locally observed Qt provider error. An Invoke failure can have
            # side effects: only retry if the exact bound row AND menu survive.
            if getattr(error,'hresult',None)!=-2147220991:raise
            invoke_error=error
            root,_=recheck()
            matching=[n for n in menu_items(adapter.auto,root,window['pid'])
                if tuple(n.GetRuntimeId())==tuple(selected.GetRuntimeId())]
            if len(matching)!=1:raise error
            selected=matching[0]
    if pattern is None or invoke_error is not None:
        rect=_rect(selected)
        center=((rect[0]+rect[2])//2,(rect[1]+rect[3])//2)
        pointed=adapter.auto.ControlFromPoint(*center)
        point_runtime=tuple(pointed.GetRuntimeId()) if pointed is not None else None
        adapter.voice_diagnostic.update(menu_rect=rect,center=list(center))
        hits=[]
        matches_point=False
        for _ in range(4):
            if pointed is None:break
            hits.append({'class':str(_get(pointed,'ClassName')),'runtime':pointed.GetRuntimeId(),
                'pid':_get(pointed,'ProcessId'),'hwnd':_get(pointed,'NativeWindowHandle'),'rect':_rect(pointed)})
            if tuple(pointed.GetRuntimeId())==tuple(selected.GetRuntimeId()):matches_point=True;break
            pointed=pointed.GetParentControl()
        adapter.voice_diagnostic['hit_chain']=hits
        if not matches_point and qt_menu:
            # Qt hit testing exposes the popup container instead of its item.
            # Accept only the exact item's XMenu ancestor, backed by the native
            # window under this point and its verified main-window ownership.
            parent=selected.GetParentControl()
            for _ in range(4):
                if parent is None:break
                handle=_get(parent,'NativeWindowHandle',0)
                if (str(_get(parent,'ClassName'))=='mmui::XMenu' and handle
                        and _get(parent,'ProcessId')==window['pid']
                        and tuple(parent.GetRuntimeId())==point_runtime):
                    popup=_rect(parent)
                    matches_point=bool(popup and popup[0]<=center[0]<popup[2] and popup[1]<=center[1]<popup[3]
                        and backend.gui.WindowFromPoint(center)==handle
                        and backend.gui.GetWindow(handle,backend.con.GW_OWNER)==window['hwnd']
                        and backend._exe(handle)[0]==window['pid'])
                    if matches_point:adapter.voice_diagnostic['hit_mode']='owned_qt_menu_container'
                    break
                parent=parent.GetParentControl()
        if not matches_point:raise WeChatError('voice_menu_obscured','转文字菜单被遮挡，已停止本次操作。')
        guard()
        selected.Click(simulateMove=False,waitTime=0)
        method='uia_menu_click_after_invoke_error' if invoke_error is not None else 'uia_menu_click'
    return {'status':'processing','method':method,'message':message,'elapsed_ms':round((time.perf_counter()-started)*1000,2)}

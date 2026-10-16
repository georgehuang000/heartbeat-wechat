from types import SimpleNamespace as NS
import pytest
from galchat.visual_wechat import Win32ScreenBackend
from galchat.wechat import WeChatError


def backend(owners):
    value=Win32ScreenBackend.__new__(Win32ScreenBackend)
    value._dpi=lambda:None
    value._cloaked=lambda hwnd:False
    value._exe=lambda hwnd:(123,'C:/fixture/Weixin.exe')
    value.con=NS(GW_OWNER=4)
    value.gui=NS(EnumWindows=lambda fn,arg:[fn(h,arg) for h in owners],
        GetWindowText=lambda hwnd:'Weixin',IsWindowVisible=lambda hwnd:True,
        GetWindow=lambda hwnd,flag:owners[hwnd],GetWindowRect=lambda hwnd:(0,0,600,500),
        IsIconic=lambda hwnd:False)
    return value


def test_owned_weixin_menu_is_not_another_account_window():
    assert backend({7:42,42:0}).window()['hwnd']==42


def test_two_real_main_windows_still_reject_as_ambiguous():
    with pytest.raises(WeChatError) as error:backend({7:0,42:0}).window()
    assert error.value.code=='ambiguous_window'


def test_popup_alone_cannot_become_the_chat_window():
    with pytest.raises(WeChatError) as error:backend({7:42}).window()
    assert error.value.code=='not_running'

"""Resize calibrated rectangles using fixed chrome margins, not stretched UI."""
from copy import deepcopy
from .visual_wechat import VisualWeChatAdapter
from .wechat import WeChatError

class AdaptiveVisualAdapter(VisualWeChatAdapter):
    def configure(self, config):
        value = super().configure(config)
        self.reference = deepcopy(value)
        return value

    def _capture(self):
        if not self.calibration:
            raise WeChatError('calibration_required', '请在设置中框选微信聊天区一次，之后自动跟随联系人。')
        window = self.backend.window()
        w, h = window['rect'][2]-window['rect'][0], window['rect'][3]-window['rect'][1]
        base = self.reference
        config = {**base, 'width': w, 'height': h}
        for key in ('region','title_region','input_region'):
            x1,y1,x2,y2 = base[key]
            if key == 'input_region':
                y1,y2 = h-(base['height']-y1), h-(base['height']-y2)
            elif key == 'region':
                y2 = h-(base['height']-y2)
            x2 = w-(base['width']-x2)
            if not (0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h):
                raise WeChatError('calibration_changed', '微信窗口过小，无法保留完整聊天区。')
            config[key] = [x1,y1,x2,y2]
        if config['title_region'][3] >= config['input_region'][1]:
            raise WeChatError('calibration_changed', '微信聊天区太小，请放大一点。')
        self.calibration = config
        return window, self.backend.capture(window, config['region'])

"""Small, explicit natural-language routes for enrolled phones.

Message bodies are opaque text: commas and quoted commands are never planned.
Unknown/ambiguous requests return a question rather than selecting a device.
"""
from __future__ import annotations

import re


def phone_intent(runtime, goal: str, context=None):
    phones = [d for d in runtime.devices() if d.get('adapter') == 'endpoint'
              and d.get('metadata', {}).get('platform') == 'android']
    aliases = ['我的手机', '手机'] + [str(d[k]) for d in phones for k in ('device_id', 'name') if d.get(k)]
    target = '(?:' + '|'.join(re.escape(a) for a in sorted(set(aliases), key=len, reverse=True)) + ')'
    text = re.sub(r'^(?:请帮我|帮我|请)\s*', '', goal.strip())
    if re.match(r'不要|别|不用|取消', text) and any(a in text for a in aliases):
        return _reply(goal, '未下发新的手机任务。取消已排队任务请使用对应 Mission 的取消操作。')
    requested = (context or {}).get('device_id')
    matched_target = None
    capability, arguments = None, {}
    ui_goal = False
    patterns = [
        ('speak', rf'(?:让|用)?\s*(?P<target>{target})\s*(?:给我)?(?:播报|朗读|读出)(?:这句话)?[\s：:,，]*(?P<body>.*)'),
        ('notify', rf'通知(?:一下)?\s*(?P<target>{target})[\s：:,，]*(?P<body>.*)'),
        ('notify', rf'(?:给|向)\s*(?P<target>{target})\s*(?:发|发送)(?:一条)?通知[\s：:,，]*(?P<body>.*)'),
    ]
    for cap, pattern in patterns:
        match = re.fullmatch(pattern, text, flags=re.DOTALL)
        if match:
            matched_target = match['target']
            body = match['body'].strip()
            if len(body) >= 2 and (body[0], body[-1]) in [('“', '”'), ('"', '"'), ('「', '」'), ("'", "'")]:
                body = body[1:-1]
            capability = cap
            if cap == 'speak' and not body:
                return _reply(goal, '要让手机播报什么内容？', needs_input=True)
            arguments = {'text': body or '这是一条来自 Agent 的通知。'}
            if cap == 'notify':
                arguments['title'] = 'Agent 通知'
            break
    if capability is None:
        if not any(a in text for a in aliases) and not requested and not re.search(r'打开.*设置', text):
            return None
        if re.search(r'打开.*设置|设置.*电池|点击|滑动|输入框|读取.*界面|读屏', text):
            ui_goal = True
            capability = 'ui.observe'
        elif re.search(r'截图|截.{0,2}图|捕捉.*屏幕|捕获.*屏幕|屏幕快照', text):
            capability = 'capture_screen'
        elif re.search(r'电量|电池', text):
            capability = 'read_battery'
        elif re.search(r'硬件|配置|型号|系统信息|内存|存储|传感器|网络状态|CPU|处理器', text, re.IGNORECASE):
            capability = 'device_info'
        else:
            return _reply(goal, '目前支持手机通知、播报、硬件信息、电量和授权截图。请说明要执行哪一项。', needs_input=True)
        named = [d for d in phones if d['device_id'] in text or d['name'] in text]
    else:
        named = [d for d in phones if matched_target in (d['device_id'], d['name'])]
    if requested:
        named = [d for d in phones if d['device_id'] == requested]
        if not named:
            return _reply(goal, '指定的手机尚未配对。', needs_input=True)
    candidates = named or phones
    if len(candidates) != 1:
        return _reply(goal, '尚未连接 Android 手机。' if not candidates else '已连接多台手机，请指定设备名称或 device_id。',
                      needs_input=True, candidates=[{'device_id': d['device_id'], 'name': d['name']} for d in candidates])
    device = candidates[0]
    if capability not in [c['name'] for c in device['capabilities']]:
        return _reply(goal, f"{device['name']} 尚未启用 {capability}，请更新 App 并由主人授予该能力。", needs_input=True)
    if ui_goal:
        return _reply(goal, '界面任务由上层 Agent 规划。请通过 iot_call 逐步调用 app.launch / ui.observe / ui.tap / ui.input / ui.swipe / ui.back，读取 command_status 回执后决定下一步。',
                      execution='planning_required', device_id=device['device_id'],
                      available_capabilities=[c['name'] for c in device['capabilities'] if c['name'].startswith(('ui.', 'app.'))])
    result = runtime.call(device['device_id'], capability, arguments, source='phone.intent')
    command = result['result']
    detail = '已入队，等待手机回执。'
    if capability == 'capture_screen':
        detail += '请先在手机 App 开启屏幕共享并完成系统授权；锁屏或系统停止共享后需重新授权。'
    return {'goal': goal, 'ok': True, 'summary': detail, 'affected': [device['device_id']],
            'execution': 'pending', 'command_id': command['command_id'],
            'steps': [{'n': 1, 'title': device['name'], 'detail': detail, 'tool': 'iot_call',
                       'args': {'device_id': device['device_id'], 'capability': capability, 'arguments': arguments}}]}


def _reply(goal, summary, **extra):
    return {'goal': goal, 'ok': False, 'summary': summary, 'steps': [], 'affected': [], **extra}

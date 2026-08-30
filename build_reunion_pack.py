from pathlib import Path
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.section import WD_SECTION
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

OUT = Path('/Users/ericlin/Documents/GitHub/AI Products/毕业20周年华工聚会文件包')
OUT.mkdir(parents=True, exist_ok=True)

BLUE = '1F4E79'
NAVY = '17365D'
GOLD = 'B07B19'
PALE = 'EAF2F8'
GRAY = 'F2F4F7'
TEXT = '222222'
FONT = 'Hiragino Sans GB'

def set_font(run, size=10.5, bold=False, color=TEXT, italic=False):
    run.font.name = FONT
    run._element.rPr.rFonts.set(qn('w:ascii'), FONT)
    run._element.rPr.rFonts.set(qn('w:hAnsi'), FONT)
    run._element.rPr.rFonts.set(qn('w:eastAsia'), FONT)
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic
    run.font.color.rgb = RGBColor.from_string(color)

def set_cell_shading(cell, fill):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = tcPr.find(qn('w:shd'))
    if shd is None:
        shd = OxmlElement('w:shd')
        tcPr.append(shd)
    shd.set(qn('w:fill'), fill)

def set_cell_margins(cell, top=80, start=120, bottom=80, end=120):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcMar = tcPr.first_child_found_in('w:tcMar')
    if tcMar is None:
        tcMar = OxmlElement('w:tcMar')
        tcPr.append(tcMar)
    for m, value in [('top', top), ('start', start), ('bottom', bottom), ('end', end)]:
        node = tcMar.find(qn(f'w:{m}'))
        if node is None:
            node = OxmlElement(f'w:{m}')
            tcMar.append(node)
        node.set(qn('w:w'), str(value))
        node.set(qn('w:type'), 'dxa')

def set_table_geometry(table, widths):
    # Exact fixed DXA geometry required for predictable Word/LibreOffice rendering.
    tbl = table._tbl
    tblPr = tbl.tblPr
    tblW = tblPr.first_child_found_in('w:tblW')
    if tblW is None:
        tblW = OxmlElement('w:tblW'); tblPr.append(tblW)
    tblW.set(qn('w:w'), str(sum(widths))); tblW.set(qn('w:type'), 'dxa')
    layout = tblPr.first_child_found_in('w:tblLayout')
    if layout is None:
        layout = OxmlElement('w:tblLayout'); tblPr.append(layout)
    layout.set(qn('w:type'), 'fixed')
    ind = tblPr.first_child_found_in('w:tblInd')
    if ind is None:
        ind = OxmlElement('w:tblInd'); tblPr.append(ind)
    ind.set(qn('w:w'), '120'); ind.set(qn('w:type'), 'dxa')
    grid = tbl.tblGrid
    for col, width in zip(grid.gridCol_lst, widths):
        col.set(qn('w:w'), str(width))
    for row in table.rows:
        for cell, width in zip(row.cells, widths):
            tcPr = cell._tc.get_or_add_tcPr()
            tcW = tcPr.find(qn('w:tcW'))
            if tcW is None:
                tcW = OxmlElement('w:tcW'); tcPr.append(tcW)
            tcW.set(qn('w:w'), str(width)); tcW.set(qn('w:type'), 'dxa')
            set_cell_margins(cell)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER

def style_table(table, widths, header=True, font_size=9):
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.autofit = False
    set_table_geometry(table, widths)
    for r, row in enumerate(table.rows):
        for cell in row.cells:
            if header and r == 0:
                set_cell_shading(cell, BLUE)
                c = 'FFFFFF'; b = True
            elif r % 2 == 0:
                set_cell_shading(cell, 'F8FBFD')
                c = TEXT; b = False
            else:
                c = TEXT; b = False
            for p in cell.paragraphs:
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.line_spacing = 1.1
                for run in p.runs:
                    set_font(run, font_size, b, c)

def add_table(doc, headers, rows, widths, font_size=9):
    table = doc.add_table(rows=1, cols=len(headers))
    for i, v in enumerate(headers):
        table.rows[0].cells[i].text = str(v)
    for row in rows:
        cells = table.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = str(v)
    style_table(table, widths, True, font_size)
    doc.add_paragraph().paragraph_format.space_after = Pt(1)
    return table

def set_doc_defaults(doc, header_label):
    sec = doc.sections[0]
    sec.top_margin = Inches(0.75); sec.bottom_margin = Inches(0.75)
    sec.left_margin = Inches(0.8); sec.right_margin = Inches(0.8)
    sec.header_distance = Inches(0.3); sec.footer_distance = Inches(0.3)
    normal = doc.styles['Normal']
    normal.font.name = FONT
    normal._element.rPr.rFonts.set(qn('w:eastAsia'), FONT)
    normal.font.size = Pt(10.5)
    pf = normal.paragraph_format
    pf.space_after = Pt(5); pf.line_spacing = 1.25
    for name, size, color, before, after in [('Heading 1', 16, BLUE, 18, 8), ('Heading 2', 13, BLUE, 14, 7), ('Heading 3', 11.5, NAVY, 10, 5)]:
        st = doc.styles[name]; st.font.name = FONT; st._element.rPr.rFonts.set(qn('w:eastAsia'), FONT)
        st.font.size = Pt(size); st.font.color.rgb = RGBColor.from_string(color); st.font.bold = True
        st.paragraph_format.space_before = Pt(before); st.paragraph_format.space_after = Pt(after)
        st.paragraph_format.keep_with_next = True
    hp = sec.header.paragraphs[0]
    hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    r = hp.add_run(header_label); set_font(r, 8.5, False, '6B7280')
    fp = sec.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = fp.add_run('华南理工大学毕业20周年聚会 | 2026年11月'); set_font(r, 8.5, False, '6B7280')

def add_title(doc, kicker, title, subtitle):
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER; p.paragraph_format.space_before = Pt(32); p.paragraph_format.space_after = Pt(6)
    r = p.add_run(kicker); set_font(r, 11, True, GOLD)
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER; p.paragraph_format.space_after = Pt(8)
    r = p.add_run(title); set_font(r, 26, True, NAVY)
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER; p.paragraph_format.space_after = Pt(22)
    r = p.add_run(subtitle); set_font(r, 12, False, '525252')

def add_callout(doc, title, text):
    table = doc.add_table(rows=1, cols=1)
    set_table_geometry(table, [9360])
    cell = table.cell(0, 0); set_cell_shading(cell, 'EEF5FB')
    p = cell.paragraphs[0]; p.paragraph_format.space_after = Pt(3)
    r = p.add_run(title + '  '); set_font(r, 10.5, True, NAVY)
    r = p.add_run(text); set_font(r, 10.5, False, TEXT)
    doc.add_paragraph().paragraph_format.space_after = Pt(0)

def add_para(doc, text, bold_lead=None):
    p = doc.add_paragraph()
    if bold_lead and text.startswith(bold_lead):
        r = p.add_run(bold_lead); set_font(r, 10.5, True, NAVY)
        r = p.add_run(text[len(bold_lead):]); set_font(r)
    else:
        r = p.add_run(text); set_font(r)
    return p

def add_check(doc, text):
    p = doc.add_paragraph(style='List Bullet')
    p.paragraph_format.space_after = Pt(3)
    r = p.add_run(text); set_font(r, 10.5)

def plan_doc():
    doc = Document(); set_doc_defaults(doc, '执行方案 | 筹委会版本')
    add_title(doc, '廿年再聚华园', '华南理工大学毕业20周年聚会执行方案', '2026年11月7日（周六）—11月8日（周日） | 五山校区 | 40人基准版')
    add_callout(doc, '方案结论', '以“回校园、见老师、深度交流、留存记忆”为主线，标准双人拼住方案目标成本为每人¥845（不含往返广州交通），控制线为每人¥1000。')
    doc.add_heading('一、活动定位与目标', level=1)
    add_table(doc, ['项目', '执行口径'], [
        ['活动主题', '廿年再聚华园｜我们仍是那个班'],
        ['适用人数', '30—50人；本预算以40人测算'],
        ['活动时间', '2026年11月7日09:00至11月8日13:00，2天1夜'],
        ['活动地点', '华南理工大学五山校区；班会场地优先学院会议室/报告厅，未获批则使用近校酒店会议室'],
        ['活动成果', '班级合影、老师合影、纪念视频、电子纪念册、未来互助清单、2036时光信箱'],
        ['体验原则', '不冗长、不强制表演、不劝酒；把时间留给校园记忆与真实交流。'],
    ], [1900, 7460], 9.3)
    doc.add_heading('二、活动总流程', level=1)
    add_table(doc, ['日期', '时间', '活动', '执行内容'], [
        ['11/7 周六', '09:00–09:40', '签到与领取物料', '校门附近集合；发胸牌、流程卡、班旗贴纸；拍摄今昔对照单人照。'],
        ['11/7 周六', '09:40–11:30', '重走华园', '两组校园记忆路线；旧教学区/学院楼外围、西湖周边、宿舍区外围等，以校方批准开放区域为准。'],
        ['11/7 周六', '11:45–13:00', '怀旧午餐', '校方批准的校园餐厅或校门附近餐厅；按宿舍/社团/课程小组混坐。'],
        ['11/7 周六', '13:00–13:30', '全班大合影', '全班、老师、家属分别合影；复刻毕业照姿势。'],
        ['11/7 周六', '14:00–16:10', '20周年班会', '老师发言、点名近况、同学分享、老照片竞猜、未到场祝福、未来倡议。'],
        ['11/7 周六', '16:10–17:00', '时光信箱', '写给2036年的信；录制20秒视频祝福。'],
        ['11/7 周六', '17:00–18:00', '入住与休整', '按预先排定的双人房入住。'],
        ['11/7 周六', '18:30–21:00', '20周年正式晚宴', '40人约4—5桌；老师主桌；统一举杯一次，之后自由交流。'],
        ['11/7 周六', '21:00–22:20', '华园夜话', '老照片猜猜看、班歌、自由麦、播放未到场同学祝福；不设强制节目。'],
        ['11/8 周日', '08:30–09:30', '早餐与自由晨走', '酒店早餐；自愿参加。'],
        ['11/8 周日', '09:45–10:45', '小组深聊', '按行业/兴趣分桌，形成“未来互助清单”。'],
        ['11/8 周日', '10:45–11:20', '下一程约定', '确定班级通讯录维护人、年度线上会、五年后返校安排；公布财务初表。'],
        ['11/8 周日', '11:30–12:45', '送别午餐', '轻松粤菜或早茶式午餐，适配外地同学赶车。'],
        ['11/8 周日', '12:45–13:00', '结束与送站', '发布电子合影下载链接，按车次分批送地铁/高铁站。'],
    ], [1300, 1500, 1850, 4710], 8.6)
    doc.add_heading('三、20周年班会脚本（14:00–16:10）', level=1)
    add_table(doc, ['时长', '环节', '主持提示'], [
        ['8分钟', '开场视频与欢迎', '放毕业照与老照片；说明今天“少流程、多相处”的基调。'],
        ['10分钟', '老师发言', '提前确认老师到场及称呼；准备鲜花、合影相框、纪念册。'],
        ['25分钟', '点名近况', '每人一句：姓名、现居城市、最近最想分享的一件事；主持人把控节奏。'],
        ['20分钟', '20年里的三个变化', '事业、家庭、自己，各邀请3位同学分享，不做攀比式介绍。'],
        ['12分钟', '老照片竞猜', '每题不超过90秒，调动气氛即可。'],
        ['8分钟', '未到场同学祝福', '提前收集视频，无法收集时可读文字。'],
        ['7分钟', '时光信箱与下一次相约', '说明封存方式、开启日期、线上联系机制。'],
    ], [1100, 2200, 6060], 9)
    doc.add_heading('四、预算与收款规则', level=1)
    add_table(doc, ['项目', '人均预算（元）', '说明'], [
        ['双人拼住一晚（含早）', '235', '按双床/双人间均摊，最终以合同价结算。'],
        ['校园/会务场地及设备', '40', '校方场地或近校会议室的综合预留。'],
        ['首日午餐', '55', '校园周边或获批餐厅。'],
        ['茶歇、饮水、夜话轻食', '35', '会议茶歇、晚间软饮及轻食。'],
        ['20周年正式晚宴', '230', '含软饮和适量酒水；不做高档酒水配置。'],
        ['次日送别午餐', '95', '早茶或粤菜午餐。'],
        ['影像、纪念物料', '75', '摄影摄像、胸牌、班旗、电子纪念册。'],
        ['保险、接驳与机动', '80', '小额保险、临时交通、应急耗材及不可预见支出。'],
        ['合计', '845', '标准双人拼住方案；往返广州交通和单人房差价不含在内。'],
    ], [4000, 1600, 3760], 9.2)
    add_callout(doc, '收款建议', '报名时收¥500定金；活动前两周锁定名单并收尾款。取消后如名额能转让则全退；不可退的酒店/餐费按实际明细扣除。结束后7天内公开收入、支出、结余与票据摘要。')
    doc.add_heading('五、筹备节奏与责任分工', level=1)
    add_table(doc, ['时间', '关键动作', '交付物', '主责'], [
        ['现在—8月中旬', '建立筹委会，确定日期与人数目标', '名单、群规、报名方案', '总协调'],
        ['8月下旬', '对接学院/校友工作老师，提交返校需求', '场地、校史馆、车辆、老师邀请需求单', '校方联络'],
        ['9月上旬', '第一轮报名，统计到达、住宿、饮食、校友卡', '报名台账v1', '报名住宿组'],
        ['9月中旬', '办理校友卡；询价并暂锁酒店、晚宴、摄影', '供应商比价表、预算v1', '外联/财务'],
        ['9月底', '收定金、形成校园路线与雨天预案', '锁定名单v1、现场动线', '总协调/现场组'],
        ['10月上旬', '收集老照片和祝福，制作视频与纪念册', '班会PPT、视频粗剪', '内容影像组'],
        ['10月20日前', '锁定人数、房型、菜单、老师行程、摄影', '供应商确认单', '总协调'],
        ['10月下旬', '提交最终团体/车辆名单，制作全部物料', '入校清单、胸牌、桌牌、班旗', '校方联络/现场组'],
        ['活动前7天', '发《行前通知》，收尾款', '行前通知、联系人表', '总协调/财务'],
        ['活动前1天', '踩点、清点物料、确认关键到场人', '现场执行单', '全体筹委'],
    ], [1400, 2900, 2900, 2160], 8.5)
    doc.add_heading('六、现场角色清单', level=1)
    for text in [
        '总协调：统筹决策、老师邀请、时间控制与突发事项最终判断。',
        '校方联络：学院、校友会、场地、校史馆、入校与车辆清单。',
        '报名住宿：报名表、拼房、抵离、送站及家属信息。',
        '财务：询价、收款、付款审批、票据与公开账目。',
        '内容主持：班会脚本、PPT、视频、互动与老师致辞衔接。',
        '影像物料：照片征集、摄影摄像、纪念册、胸牌、班旗。',
        '现场安全：签到、队伍行进、医疗用品、雨天切换与失联处理。',
    ]: add_check(doc, text)
    doc.add_page_break()
    doc.add_heading('七、校方协调与风险预案', level=1)
    add_para(doc, '校方协调原则：由学院校友工作老师或校友会统一提交“返校聚会＋场地＋团体参观＋车辆”需求。不要把40人的活动拆成个人预约。')
    add_para(doc, '入校提醒：按学校公开规则，周末游客入校通常为09:00—18:00，团体参观（15人以上）通常需至少提前3个工作日以单位公函申请；校友应尽早办理电子校友卡。具体开放区域、校门及车辆规则以活动前校方最终批复为准。')
    add_table(doc, ['情形', '切换方案'], [
        ['下雨', '校园漫步改为“校园记忆图册＋老照片讲述＋获批室内参观”；合影改至会议室或酒店大厅。'],
        ['校内场地未获批', '校园只安排合规参观与合影；班会、晚宴、夜话移至五山站附近酒店会议室。'],
        ['老师无法到场', '提前录制祝福；现场安排视频播放与同学代表致谢。'],
        ['人数低于30人', '缩小摄影与场地配置，优先保留晚宴、班会、校园漫步三项。'],
        ['人数高于50人', '增加签到、摄影和领队志愿者；校园漫步分组同步进行。'],
    ], [2000, 7360], 9.2)
    doc.save(OUT / '01_华工毕业20周年聚会执行方案.docx')

def templates_doc():
    doc = Document(); set_doc_defaults(doc, '对外沟通模板 | 可直接复制')
    add_title(doc, '廿年再聚华园', '对外沟通与报名模板', '微信群邀请文案｜报名表字段｜校方申请函｜行前通知')
    doc.add_heading('A. 微信群首轮邀请文案', level=1)
    add_callout(doc, '可直接发送', '【廿年再聚华园】亲爱的同学们：转眼毕业已20年。我们计划于2026年11月7日（周六）—8日（周日）回华南理工大学五山校区，进行2天1夜的毕业20周年聚会。活动包括重走校园、老师见面、20周年班会、正式晚宴与同学深聊。标准双人拼住方案预计¥845/人（不含往返广州交通），控制在¥1000以内。请大家于【填写日期】前完成意向登记；能回来，就是最好的礼物。报名链接：【填写链接】。')
    doc.add_heading('B. 报名表字段', level=1)
    add_table(doc, ['分组', '字段', '填写说明'], [
        ['基本信息', '姓名、原班级/学号、手机号、现居城市', '姓名用于胸牌；手机仅供筹委会联络。'],
        ['参与信息', '是否参会、是否携家属、抵达/离开时间', '便于安排座位、接驳和送站。'],
        ['住宿信息', '双人拼住/自理住宿、室友偏好、是否需要单人房', '单人房差价自理；优先尊重拼住偏好。'],
        ['入校信息', '是否已办电子校友卡、是否驾车及车牌', '仅在校方确有需要时单独收集身份证信息。'],
        ['餐饮健康', '饮食禁忌、过敏、行动不便或需协助事项', '仅用于现场保障，不在群内公开。'],
        ['影像授权', '是否同意影像用于班级纪念册', '可选择“仅群内使用”或“不公开”。'],
        ['时光素材', '一张旧照、最想见的人、想讲的一件校园往事', '用于班会互动和纪念视频。'],
    ], [1550, 2700, 5110], 9.1)
    doc.add_heading('C. 致学院/校友工作老师的申请函模板', level=1)
    add_para(doc, '尊敬的【学院名称】校友工作老师：')
    add_para(doc, '您好！我们是【入学年份/毕业年份】【专业/班级】校友，拟于2026年11月7日—8日回母校举行毕业20周年返校聚会，预计参加校友【40】人，老师及家属约【　】人。为规范、有序开展活动，现诚恳申请学院协助。')
    add_table(doc, ['申请事项', '需求说明'], [
        ['活动场地', '11月7日14:00—16:10，容纳约【　】人的会议室/报告厅，用于班会、老师交流与影像播放。'],
        ['校园参观', '组织校友在校内公共开放区域重走校园；如条件允许，希望预约校史馆或相关人文景观。'],
        ['入校与车辆', '校友已/将办理电子校友卡；如有车辆入校需求，将按学校流程提前提交车牌与人员名单。'],
        ['老师联络', '恳请协助转达返校邀请；老师的时间安排以其方便为先。'],
        ['其他', '【填写：如合影点、餐饮建议等】。'],
    ], [2100, 7260], 9.2)
    add_para(doc, '我们承诺遵守学校校园管理规定，仅在获批区域开展活动，服从现场管理，不影响正常教学科研秩序。随函附活动初步流程和联系人信息，敬请指导。')
    add_para(doc, '联系人：【姓名】  电话：【手机号】  邮箱：【邮箱】\n【班级名称】毕业20周年筹备组\n【填写日期】')
    doc.add_heading('D. 活动前7天行前通知模板', level=1)
    add_callout(doc, '可直接发送', '【行前通知】本周末我们华园见！\n1. 集合：11月7日09:00，【校门/集合点，以最终校方通知为准】；迟到请直接联系【现场负责人+电话】。\n2. 入校：请携带身份证并提前确认电子校友卡；驾车同学按获批车牌入校，建议优先地铁/打车。\n3. 住宿：入住【酒店名称】，双人房名单见群公告；单人房差价请自行补齐。\n4. 衣着：建议舒适步行鞋；合影建议穿纯色或班服。广州11月天气多变，请带轻外套和雨具。\n5. 安全：如有过敏、行动不便、临时变更行程，请第一时间联系【安全负责人+电话】。\n6. 温馨提示：活动不劝酒；请尊重同学隐私，未经同意不要将个人近况和照片公开发布。')
    doc.add_heading('E. 老师邀请短信模板', level=1)
    add_callout(doc, '可直接发送', '【老师称呼】您好！我们是【班级】的学生。今年正值毕业20周年，计划于11月7日下午回华工举行小型班会，大家都很希望能当面向您问好。若您方便，诚邀您于【时间】莅临【地点】与同学们见面；若时间不便，也请您保重身体，我们会把您的祝福带给全班。联系人：【姓名、电话】。')
    doc.save(OUT / '03_群公告报名校方申请模板.docx')

if __name__ == '__main__':
    plan_doc()
    templates_doc()
    print(OUT)

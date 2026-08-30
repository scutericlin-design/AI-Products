import fs from 'node:fs/promises';
import { SpreadsheetFile, Workbook } from '@oai/artifact-tool';

const outDir = '/Users/ericlin/Documents/GitHub/AI Products/毕业20周年华工聚会文件包';
const workbook = Workbook.create();
const navy = '#17365D';
const blue = '#1F4E79';
const lightBlue = '#EAF2F8';
const pale = '#F7FAFC';
const gray = '#667085';
const border = '#D0D7DE';
const white = '#FFFFFF';

function title(sheet, range, text) {
  sheet.getRange(range).merge();
  sheet.getRange(range.split(':')[0]).values = [[text]];
  sheet.getRange(range).format = {
    fill: navy,
    font: { bold: true, color: white, size: 16, name: 'Microsoft YaHei' },
    horizontalAlignment: 'left', verticalAlignment: 'center'
  };
  sheet.getRange(range).format.rowHeight = 30;
}
function section(sheet, range, text) {
  sheet.getRange(range).merge();
  sheet.getRange(range.split(':')[0]).values = [[text]];
  sheet.getRange(range).format = {
    fill: lightBlue,
    font: { bold: true, color: navy, size: 11, name: 'Microsoft YaHei' },
    verticalAlignment: 'center'
  };
  sheet.getRange(range).format.rowHeight = 22;
}
function header(sheet, range) {
  sheet.getRange(range).format = {
    fill: blue, font: { bold: true, color: white, name: 'Microsoft YaHei' },
    horizontalAlignment: 'center', verticalAlignment: 'center', wrapText: true,
    borders: { preset: 'all', style: 'thin', color: border }
  };
  sheet.getRange(range).format.rowHeight = 32;
}
function tableStyle(sheet, range) {
  sheet.getRange(range).format = {
    font: { name: 'Microsoft YaHei', size: 10, color: '#222222' },
    verticalAlignment: 'center', wrapText: true,
    borders: { preset: 'all', style: 'thin', color: border }
  };
}
function widths(sheet, map) {
  for (const [col, width] of Object.entries(map)) sheet.getRange(`${col}:${col}`).format.columnWidth = width;
}
function notes(sheet, range, text) {
  sheet.getRange(range).merge();
  sheet.getRange(range.split(':')[0]).values = [[text]];
  sheet.getRange(range).format = { fill: '#FFF8E8', font: { color: '#7A5A00', italic: true, name: 'Microsoft YaHei' }, wrapText: true, verticalAlignment: 'center' };
  sheet.getRange(range).format.rowHeight = 38;
}

// 预算
const budget = workbook.worksheets.add('预算'); budget.showGridLines = false;
title(budget, 'A1:F1', '华工毕业20周年聚会｜预算与实际结算');
section(budget, 'A3:F3', '基础参数（可修改黄色单元格）');
budget.getRange('A4:B7').values = [
  ['预计人数', 40], ['标准收费/人（元）', 845], ['定金/人（元）', 500], ['单人房差价（元）', 230]
];
budget.getRange('A4:A7').format = { fill: pale, font: { bold: true, color: navy, name: 'Microsoft YaHei' }, borders: { preset: 'all', style: 'thin', color: border } };
budget.getRange('B4:B7').format = { fill: '#FFF2CC', font: { bold: true, name: 'Microsoft YaHei' }, numberFormat: '#,##0', borders: { preset: 'all', style: 'thin', color: border } };
section(budget, 'A9:F9', '标准方案预算（按双人拼住测算）');
budget.getRange('A10:F10').values = [['项目', '人均预算', '预算人数', '预算总额', '实际总额', '差异']]; header(budget, 'A10:F10');
budget.getRange('A11:C18').values = [
  ['双人拼住一晚（含早）', 235, 40], ['校园/会务场地及设备', 40, 40], ['首日午餐', 55, 40], ['茶歇、饮水、夜话轻食', 35, 40], ['20周年正式晚宴', 230, 40], ['次日送别午餐', 95, 40], ['影像、纪念物料', 75, 40], ['保险、接驳与机动', 80, 40]
];
budget.getRange('C11:C18').formulas = Array.from({length:8}, () => ["=$B$4"]);
budget.getRange('D11:D18').formulas = Array.from({length:8}, (_, i) => [`=B${11+i}*C${11+i}`]);
budget.getRange('E11:E18').values = Array.from({length:8}, () => [0]);
budget.getRange('F11:F18').formulas = Array.from({length:8}, (_, i) => [`=E${11+i}-D${11+i}`]);
budget.getRange('A19:F19').values = [['合计', '', '', '=SUM(D11:D18)', '=SUM(E11:E18)', '=SUM(F11:F18)']];
tableStyle(budget, 'A11:F19');
budget.getRange('A19:F19').format = { fill: lightBlue, font: { bold: true, color: navy, name: 'Microsoft YaHei' }, borders: { preset: 'all', style: 'thin', color: border } };
budget.getRange('B11:F19').format.numberFormat = '#,##0';
budget.getRange('E11:E18').format.fill = '#FFF2CC';
notes(budget, 'A21:F22', '填写方式：先用“预算”作为收款基准；活动前将各项目最终合同金额填入“实际总额”，差异会自动计算。单人房差价和个人交通不放入标准方案。');
widths(budget, {A:28,B:14,C:13,D:15,E:15,F:14});

// 报名名单
const roster = workbook.worksheets.add('报名名单'); roster.showGridLines = false;
title(roster, 'A1:R1', '华工毕业20周年聚会｜报名、住宿与收款台账');
notes(roster, 'A3:R4', '填写提示：仅筹委会维护本表。身份证等敏感信息不要在本表或微信群收集；如校方确有需要，应由专人单独、加密收集。黄色列为需要人工填写/更新的字段。');
const rosterHeaders = ['序号','姓名','原班级/学号','现居城市','手机号','是否携家属','是否参会','住宿选择','室友偏好','电子校友卡','是否驾车/车牌','饮食或协助事项','抵达时间','离开时间','缴款金额','应收金额','差额','备注'];
roster.getRange('A6:R6').values = [rosterHeaders]; header(roster, 'A6:R6');
for (let r = 7; r <= 86; r++) {
  roster.getRange(`A${r}`).formulas = [[`=IF(B${r}="","",ROW()-6)`]];
  roster.getRange(`P${r}`).formulas = [[`=IF(B${r}="","",'预算'!$B$5+IF(H${r}="单人房",'预算'!$B$7,0))`]];
  roster.getRange(`Q${r}`).formulas = [[`=IF(B${r}="","",O${r}-P${r})`]];
}
tableStyle(roster, 'A7:R86');
roster.getRange('B7:O86').format.fill = '#FFFDF2';
roster.getRange('A7:A86').format.fill = pale;
roster.getRange('P7:Q86').format.fill = '#F0F7FF';
roster.getRange('O7:Q86').format.numberFormat = '#,##0';
roster.getRange('F7:F86').dataValidation = { rule: { type: 'list', values: ['否','是'] } };
roster.getRange('G7:G86').dataValidation = { rule: { type: 'list', values: ['已报名','候补','不参加'] } };
roster.getRange('H7:H86').dataValidation = { rule: { type: 'list', values: ['双人拼住','单人房','自理住宿'] } };
roster.getRange('J7:J86').dataValidation = { rule: { type: 'list', values: ['已办','待办理','不确定'] } };
roster.getRange('O7:O86').conditionalFormats.add('cellIs', {operator:'lessThan', formula: 500, format:{fill:'#FDECEC', font:{color:'#9B1C1C'}}});
roster.freezePanes.freezeRows(6);
widths(roster, {A:7,B:12,C:16,D:12,E:14,F:11,G:11,H:13,I:14,J:12,K:17,L:21,M:17,N:17,O:13,P:13,Q:11,R:18});

// 筹备清单
const tasks = workbook.worksheets.add('筹备清单'); tasks.showGridLines = false;
title(tasks, 'A1:G1', '华工毕业20周年聚会｜筹备清单');
notes(tasks, 'A3:G4', '建议每周由总协调更新一次状态。状态：未开始 / 进行中 / 已完成 / 阻塞。');
tasks.getRange('A6:G6').values = [['阶段','截止时间','任务','交付物','负责人','状态','备注']]; header(tasks, 'A6:G6');
tasks.getRange('A7:G20').values = [
  ['启动','2026-08-15','建立筹委会并确定日期、人数目标','筹委名单、群规、报名方案','','未开始',''],
  ['校方','2026-08-31','联系学院/校友老师，提交返校需求','场地、团体参观、车辆需求单','','未开始',''],
  ['报名','2026-09-10','第一轮报名及校友卡统计','报名台账v1','','未开始',''],
  ['供应商','2026-09-20','酒店、晚宴、摄影、场地询价并暂锁','比价表、预算v1','','未开始',''],
  ['资金','2026-09-30','收定金并形成第一版预算','收款记录、预算v1','','未开始',''],
  ['内容','2026-10-08','收老照片、毕业照、老师寄语','视频素材包','','未开始',''],
  ['锁定','2026-10-20','锁人数、房型、菜单、老师行程、摄影','供应商确认单','','未开始',''],
  ['报备','2026-10-27','提交最终团体/车辆名单','入校清单','','未开始',''],
  ['物料','2026-10-31','完成胸牌、桌牌、班旗、纪念册','物料包','','未开始',''],
  ['通知','2026-10-31','发送行前通知、收尾款','行前通知、联系人表','','未开始',''],
  ['踩点','2026-11-06','现场踩点、物料清点、关键人确认','现场执行单','','未开始',''],
  ['收尾','2026-11-15','公开财务、发布影像、收集反馈','结算表、电子纪念册','','未开始',''],
  ['后续','2026-11-22','封存时光信箱，确定长期联系人','班级互助清单','','未开始',''],
  ['预留','','补充任务','','','未开始','']
];
tableStyle(tasks, 'A7:G20');
tasks.getRange('F7:F20').dataValidation = { rule: { type: 'list', values: ['未开始','进行中','已完成','阻塞'] } };
tasks.getRange('F7:F20').conditionalFormats.add('containsText', {text:'已完成',format:{fill:'#E6F4EA',font:{color:'#137333',bold:true}}});
tasks.getRange('F7:F20').conditionalFormats.add('containsText', {text:'阻塞',format:{fill:'#FCE8E6',font:{color:'#C5221F',bold:true}}});
tasks.freezePanes.freezeRows(6); widths(tasks,{A:12,B:14,C:33,D:28,E:15,F:13,G:24});

// 场地与供应商
const vendors = workbook.worksheets.add('场地供应商'); vendors.showGridLines = false;
title(vendors, 'A1:J1', '华工毕业20周年聚会｜场地与供应商比价');
notes(vendors, 'A3:J4', '请至少保留两家报价。签约前务必写清：容纳人数、用餐标准、服务费、酒水、超时费、停车、发票、取消条款。');
vendors.getRange('A6:J6').values = [['类别','供应商/场地','联系人','电话','报价日期','人数/房型','报价总额','已付金额','状态','关键条款/备注']]; header(vendors,'A6:J6');
for(let r=7;r<=26;r++) vendors.getRange(`I${r}`).dataValidation = { rule:{type:'list',values:['待询价','已报价','暂锁','已签约','淘汰']} };
tableStyle(vendors,'A7:J26'); vendors.getRange('A7:J26').format.fill = '#FFFDF2'; vendors.getRange('G7:H26').format.numberFormat = '#,##0'; vendors.freezePanes.freezeRows(6);
widths(vendors,{A:13,B:25,C:13,D:16,E:14,F:15,G:15,H:15,I:13,J:34});

// 总览
const overview = workbook.worksheets.add('总览'); overview.showGridLines = false;
title(overview, 'A1:H1', '华工毕业20周年聚会｜筹备总览');
notes(overview, 'A3:H4', '先从“报名名单”和“预算”两个工作表开始填写。本页数据自动汇总，可在每周筹委会上投屏使用。');
section(overview,'A6:H6','关键数据');
overview.getRange('A7:B11').values = [['预计人数',''],['已报名',''],['候补',''],['已收金额',''],['预计应收','']];
overview.getRange('C7:D11').values = [['标准成本/人',''],['预算总额',''],['实际支出',''],['预算差异',''],['活动日期','2026-11-07—11-08']];
overview.getRange('B7').formulas = [["='预算'!B4"]];
overview.getRange('B8').formulas = [["=COUNTIF('报名名单'!G7:G86,\"已报名\")"]];
overview.getRange('B9').formulas = [["=COUNTIF('报名名单'!G7:G86,\"候补\")"]];
overview.getRange('B10').formulas = [["=SUM('报名名单'!O7:O86)"]];
overview.getRange('B11').formulas = [["=SUM('报名名单'!P7:P86)"]];
overview.getRange('D7').formulas = [["='预算'!B5"]];
overview.getRange('D8').formulas = [["='预算'!D19"]];
overview.getRange('D9').formulas = [["='预算'!E19"]];
overview.getRange('D10').formulas = [["='预算'!F19"]];
overview.getRange('A7:D11').format = { borders:{preset:'all',style:'thin',color:border}, font:{name:'Microsoft YaHei'}, verticalAlignment:'center' };
overview.getRange('A7:A11').format = {fill:pale,font:{bold:true,color:navy,name:'Microsoft YaHei'}};
overview.getRange('C7:C11').format = {fill:pale,font:{bold:true,color:navy,name:'Microsoft YaHei'}};
overview.getRange('B7:B11').format = {fill:'#F0F7FF',font:{bold:true,color:navy,name:'Microsoft YaHei'},numberFormat:'#,##0'};
overview.getRange('D7:D10').format = {fill:'#F0F7FF',font:{bold:true,color:navy,name:'Microsoft YaHei'},numberFormat:'#,##0'};
section(overview,'A14:H14','筹备角色');
overview.getRange('A15:H22').values = [
  ['角色','姓名','职责','','角色','姓名','职责',''],
  ['总协调','','整体决策、老师邀请、时间控制','','校方联络','','学院、校友会、入校与场地',''],
  ['报名住宿','','报名、拼房、抵离、送站','','财务','','收款、付款、公开账目',''],
  ['内容主持','','班会脚本、PPT、互动','','影像物料','','摄影、视频、胸牌、纪念册',''],
  ['现场安全','','签到、动线、应急与雨天切换','','志愿者协调','','分组领队、物料分发',''],
  ['','','','','','','',''],['','','','','','','',''],['','','','','','','','']
];
header(overview,'A15:H15'); tableStyle(overview,'A16:H22'); overview.getRange('B16:B22').format.fill='#FFF2CC'; overview.getRange('F16:F22').format.fill='#FFF2CC';
section(overview,'A24:H24','官方确认提醒');
overview.getRange('A25:H28').merge(); overview.getRange('A25').values = [['请以校方最终批复为准：校友返校场地、团体参观、校史馆、人员和车辆入校。团体活动建议由学院/校友会统一协调，不以个人预约替代。相关规则和联系信息已列在Word执行方案中。']];
overview.getRange('A25:H28').format = {fill:'#FFF8E8',font:{name:'Microsoft YaHei',color:'#7A5A00'},wrapText:true,verticalAlignment:'center'};
widths(overview,{A:15,B:17,C:15,D:17,E:15,F:17,G:25,H:12});

await fs.mkdir(outDir,{recursive:true});
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(`${outDir}/02_报名预算与筹备台账.xlsx`);

const overviewPng = await workbook.render({sheetName:'总览',range:'A1:H28',scale:1.5,format:'png'});
await fs.writeFile(`${outDir}/_qa_总览.png`,new Uint8Array(await overviewPng.arrayBuffer()));
const rosterPng = await workbook.render({sheetName:'报名名单',range:'A1:R20',scale:1,format:'png'});
await fs.writeFile(`${outDir}/_qa_报名名单.png`,new Uint8Array(await rosterPng.arrayBuffer()));
const budgetPng = await workbook.render({sheetName:'预算',range:'A1:F22',scale:1.5,format:'png'});
await fs.writeFile(`${outDir}/_qa_预算.png`,new Uint8Array(await budgetPng.arrayBuffer()));

console.log((await workbook.inspect({kind:'table',range:'预算!A10:F19',include:'values,formulas',tableMaxRows:12,tableMaxCols:6})).ndjson);
console.log((await workbook.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A',options:{useRegex:true,maxResults:100},summary:'formula errors'})).ndjson);

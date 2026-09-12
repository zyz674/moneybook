# -*- coding: utf-8 -*-
"""自动分类：默认中文商户规则库 + 个人规则 + 从手工改分类中学习。"""
import re

from . import util

UNCATEGORIZED = "未分类"

BREAKDOWN_CATEGORIES = [
    "餐饮", "交通", "购物", "日用百货", "居住", "通讯", "娱乐", "医疗健康",
    "教育学习", "人情往来", "金融", "收入", "其他",
]

# 内部资金流转：转账/还款/理财等，不计入收支统计，避免把流水当成消费
NEUTRAL_KEYWORDS = [
    "转账", "信用卡还款", "还款", "还花呗", "花呗还款", "借呗还款", "备用金",
    "余额宝", "零钱通", "余利宝", "理财", "基金", "黄金", "定期", "笔笔攒",
    "提现", "零钱充值", "零钱提现", "充值到零钱", "亲情卡", "亲密付", "自动充值",
]

# (关键词, 大类, 小类, 方向约束, 优先级)  优先级数字越小越先匹配
DEFAULT_RULES = [
    # ---------- 收入 ----------
    ("工资", "收入", "工资", "in", 10),
    ("薪资", "收入", "工资", "in", 10),
    ("代发工资", "收入", "工资", "in", 10),
    ("奖金", "收入", "奖金", "in", 20),
    ("绩效", "收入", "奖金", "in", 20),
    ("报销", "收入", "报销", "in", 20),
    ("退款", "收入", "退款", "in", 20),
    ("退货", "收入", "退款", "in", 20),
    ("退款成功", "收入", "退款", "in", 20),
    ("利息", "收入", "利息", "in", 30),
    ("收益", "收入", "投资收益", "in", 30),
    ("分红", "收入", "投资收益", "in", 30),
    ("转账收款", "收入", "收款", "in", 40),
    ("收款", "收入", "收款", "in", 50),
    ("红包", "人情往来", "红包", "in", 60),
    ("生活费", "收入", "家人转入", "in", 60),
    ("兼职", "收入", "兼职", "in", 40),
    ("稿费", "收入", "兼职", "in", 40),
    ("补贴", "收入", "补贴", "in", 30),
    ("退税", "收入", "退税", "in", 30),

    # ---------- 餐饮 ----------
    ("美团外卖", "餐饮", "外卖", "", 10),
    ("饿了么", "餐饮", "外卖", "", 10),
    ("外卖", "餐饮", "外卖", "", 20),
    ("肯德基", "餐饮", "快餐", "", 15),
    ("kfc", "餐饮", "快餐", "", 15),
    ("麦当劳", "餐饮", "快餐", "", 15),
    ("华莱士", "餐饮", "快餐", "", 15),
    ("汉堡王", "餐饮", "快餐", "", 15),
    ("塔斯汀", "餐饮", "快餐", "", 15),
    ("必胜客", "餐饮", "快餐", "", 15),
    ("沙县", "餐饮", "快餐", "", 20),
    ("兰州拉面", "餐饮", "快餐", "", 20),
    ("麻辣烫", "餐饮", "快餐", "", 20),
    ("米线", "餐饮", "快餐", "", 20),
    ("黄焖鸡", "餐饮", "快餐", "", 20),
    ("星巴克", "餐饮", "咖啡饮品", "", 15),
    ("瑞幸", "餐饮", "咖啡饮品", "", 15),
    ("库迪", "餐饮", "咖啡饮品", "", 15),
    ("蜜雪冰城", "餐饮", "咖啡饮品", "", 15),
    ("喜茶", "餐饮", "咖啡饮品", "", 15),
    ("奈雪", "餐饮", "咖啡饮品", "", 15),
    ("古茗", "餐饮", "咖啡饮品", "", 15),
    ("茶百道", "餐饮", "咖啡饮品", "", 15),
    ("沪上阿姨", "餐饮", "咖啡饮品", "", 15),
    ("霸王茶姬", "餐饮", "咖啡饮品", "", 15),
    ("奶茶", "餐饮", "咖啡饮品", "", 30),
    ("咖啡", "餐饮", "咖啡饮品", "", 30),
    ("烧烤", "餐饮", "聚餐", "", 25),
    ("火锅", "餐饮", "聚餐", "", 25),
    ("海底捞", "餐饮", "聚餐", "", 20),
    ("小龙坎", "餐饮", "聚餐", "", 20),
    ("烤肉", "餐饮", "聚餐", "", 25),
    ("自助餐", "餐饮", "聚餐", "", 25),
    ("餐厅", "餐饮", "堂食", "", 40),
    ("饭店", "餐饮", "堂食", "", 40),
    ("酒楼", "餐饮", "堂食", "", 40),
    ("小吃", "餐饮", "堂食", "", 40),
    ("早餐", "餐饮", "早餐", "", 40),
    ("食堂", "餐饮", "堂食", "", 40),
    ("餐饮", "餐饮", "堂食", "", 50),
    ("食品", "餐饮", "零食", "", 60),
    ("零食", "餐饮", "零食", "", 40),
    ("水果", "餐饮", "水果", "", 40),
    ("蛋糕", "餐饮", "烘焙", "", 40),
    ("面包", "餐饮", "烘焙", "", 40),

    # ---------- 交通 ----------
    ("滴滴", "交通", "打车", "", 15),
    ("花小猪", "交通", "打车", "", 15),
    ("高德打车", "交通", "打车", "", 15),
    ("曹操出行", "交通", "打车", "", 15),
    ("t3出行", "交通", "打车", "", 15),
    ("出租车", "交通", "打车", "", 30),
    ("打车", "交通", "打车", "", 30),
    ("网约车", "交通", "打车", "", 30),
    ("地铁", "交通", "公共交通", "", 20),
    ("公交", "交通", "公共交通", "", 20),
    ("轨道交通", "交通", "公共交通", "", 20),
    ("一卡通", "交通", "公共交通", "", 25),
    ("乘车码", "交通", "公共交通", "", 25),
    ("12306", "交通", "火车", "", 15),
    ("铁路", "交通", "火车", "", 20),
    ("火车票", "交通", "火车", "", 20),
    ("航空", "交通", "机票", "", 20),
    ("机票", "交通", "机票", "", 20),
    ("携程", "交通", "出行预订", "", 30),
    ("去哪儿", "交通", "出行预订", "", 30),
    ("飞猪", "交通", "出行预订", "", 30),
    ("加油", "交通", "加油", "", 20),
    ("中石化", "交通", "加油", "", 20),
    ("中石油", "交通", "加油", "", 20),
    ("壳牌", "交通", "加油", "", 20),
    ("停车", "交通", "停车", "", 20),
    ("高速", "交通", "过路费", "", 20),
    ("etc", "交通", "过路费", "", 20),
    ("共享单车", "交通", "共享出行", "", 20),
    ("哈啰", "交通", "共享出行", "", 20),
    ("青桔", "交通", "共享出行", "", 20),
    ("美团单车", "交通", "共享出行", "", 20),
    ("洗车", "交通", "车辆养护", "", 30),
    ("车险", "交通", "车辆养护", "", 30),

    # ---------- 购物 ----------
    ("淘宝", "购物", "电商购物", "", 20),
    ("天猫", "购物", "电商购物", "", 20),
    ("京东", "购物", "电商购物", "", 20),
    ("拼多多", "购物", "电商购物", "", 20),
    ("唯品会", "购物", "电商购物", "", 20),
    ("抖音电商", "购物", "电商购物", "", 25),
    ("抖音商城", "购物", "电商购物", "", 25),
    ("得物", "购物", "电商购物", "", 25),
    ("苏宁", "购物", "电商购物", "", 25),
    ("小米", "购物", "数码家电", "", 30),
    ("华为", "购物", "数码家电", "", 30),
    ("苹果", "购物", "数码家电", "", 35),
    ("数码", "购物", "数码家电", "", 40),
    ("优衣库", "购物", "服饰鞋包", "", 20),
    ("耐克", "购物", "服饰鞋包", "", 20),
    ("阿迪达斯", "购物", "服饰鞋包", "", 20),
    ("服饰", "购物", "服饰鞋包", "", 35),
    ("服装", "购物", "服饰鞋包", "", 35),
    ("鞋", "购物", "服饰鞋包", "", 45),
    ("屈臣氏", "购物", "美妆个护", "", 25),
    ("丝芙兰", "购物", "美妆个护", "", 25),
    ("化妆品", "购物", "美妆个护", "", 35),
    ("母婴", "购物", "母婴用品", "", 30),
    ("玩具", "购物", "母婴用品", "", 40),
    ("宜家", "购物", "家居", "", 25),
    ("家居", "购物", "家居", "", 40),
    ("图书", "教育学习", "书籍", "", 30),
    ("书店", "教育学习", "书籍", "", 30),
    ("当当", "教育学习", "书籍", "", 25),

    # ---------- 日用百货 ----------
    ("超市", "日用百货", "超市", "", 30),
    ("便利店", "日用百货", "便利店", "", 30),
    ("永辉", "日用百货", "超市", "", 25),
    ("大润发", "日用百货", "超市", "", 25),
    ("沃尔玛", "日用百货", "超市", "", 25),
    ("盒马", "日用百货", "超市", "", 25),
    ("华润万家", "日用百货", "超市", "", 25),
    ("罗森", "日用百货", "便利店", "", 25),
    ("全家", "日用百货", "便利店", "", 25),
    ("711", "日用百货", "便利店", "", 25),
    ("美宜佳", "日用百货", "便利店", "", 25),
    ("菜市场", "日用百货", "生鲜", "", 30),
    ("生鲜", "日用百货", "生鲜", "", 35),
    ("买菜", "日用百货", "生鲜", "", 35),
    ("药店", "医疗健康", "药品", "", 25),
    ("药房", "医疗健康", "药品", "", 25),
    ("大药房", "医疗健康", "药品", "", 25),
    ("医院", "医疗健康", "就医", "", 25),
    ("门诊", "医疗健康", "就医", "", 25),
    ("诊所", "医疗健康", "就医", "", 25),
    ("体检", "医疗健康", "体检", "", 25),
    ("口腔", "医疗健康", "牙科", "", 30),
    ("牙科", "医疗健康", "牙科", "", 30),
    ("眼镜", "医疗健康", "视力", "", 35),
    ("挂号", "医疗健康", "就医", "", 25),

    # ---------- 居住 ----------
    ("房租", "居住", "房租", "", 15),
    ("租金", "居住", "房租", "", 20),
    ("物业", "居住", "物业", "", 20),
    ("水费", "居住", "水电燃气", "", 20),
    ("电费", "居住", "水电燃气", "", 20),
    ("燃气", "居住", "水电燃气", "", 20),
    ("天然气", "居住", "水电燃气", "", 20),
    ("供暖", "居住", "水电燃气", "", 25),
    ("国家电网", "居住", "水电燃气", "", 20),
    ("水务", "居住", "水电燃气", "", 20),
    ("家政", "居住", "家政", "", 30),
    ("保洁", "居住", "家政", "", 30),
    ("搬家", "居住", "搬家", "", 30),
    ("维修", "居住", "维修", "", 40),
    ("中介", "居住", "租房服务", "", 30),
    ("民宿", "娱乐", "旅游住宿", "", 30),
    ("酒店", "娱乐", "旅游住宿", "", 25),
    ("宾馆", "娱乐", "旅游住宿", "", 25),

    # ---------- 通讯 ----------
    ("话费", "通讯", "话费", "", 20),
    ("中国移动", "通讯", "话费", "", 20),
    ("中国联通", "通讯", "话费", "", 20),
    ("中国电信", "通讯", "话费", "", 20),
    ("流量", "通讯", "流量", "", 25),
    ("宽带", "通讯", "宽带", "", 25),
    ("云服务", "通讯", "云服务", "", 30),
    ("阿里云", "通讯", "云服务", "", 30),
    ("腾讯云", "通讯", "云服务", "", 30),
    ("服务器", "通讯", "云服务", "", 30),
    ("域名", "通讯", "云服务", "", 30),

    # ---------- 娱乐 ----------
    ("腾讯视频", "娱乐", "影音会员", "", 20),
    ("爱奇艺", "娱乐", "影音会员", "", 20),
    ("优酷", "娱乐", "影音会员", "", 20),
    ("芒果tv", "娱乐", "影音会员", "", 20),
    ("哔哩哔哩", "娱乐", "影音会员", "", 20),
    ("bilibili", "娱乐", "影音会员", "", 20),
    ("网易云音乐", "娱乐", "影音会员", "", 20),
    ("qq音乐", "娱乐", "影音会员", "", 20),
    ("spotify", "娱乐", "影音会员", "", 20),
    ("netflix", "娱乐", "影音会员", "", 20),
    ("会员", "娱乐", "会员服务", "", 60),
    ("电影", "娱乐", "电影演出", "", 25),
    ("影城", "娱乐", "电影演出", "", 25),
    ("影院", "娱乐", "电影演出", "", 25),
    ("猫眼", "娱乐", "电影演出", "", 25),
    ("淘票票", "娱乐", "电影演出", "", 25),
    ("演唱会", "娱乐", "电影演出", "", 25),
    ("ktv", "娱乐", "休闲娱乐", "", 25),
    ("剧本杀", "娱乐", "休闲娱乐", "", 25),
    ("密室", "娱乐", "休闲娱乐", "", 25),
    ("桌游", "娱乐", "休闲娱乐", "", 25),
    ("游戏", "娱乐", "游戏", "", 30),
    ("steam", "娱乐", "游戏", "", 25),
    ("王者荣耀", "娱乐", "游戏", "", 25),
    ("和平精英", "娱乐", "游戏", "", 25),
    ("原神", "娱乐", "游戏", "", 25),
    ("点券", "娱乐", "游戏", "", 30),
    ("景区", "娱乐", "旅游门票", "", 30),
    ("门票", "娱乐", "旅游门票", "", 35),
    ("旅游", "娱乐", "旅游", "", 35),
    ("健身房", "娱乐", "运动健身", "", 25),
    ("游泳", "娱乐", "运动健身", "", 30),
    ("球馆", "娱乐", "运动健身", "", 30),
    ("瑜伽", "娱乐", "运动健身", "", 30),

    # ---------- 教育 ----------
    ("学费", "教育学习", "学费", "", 15),
    ("培训", "教育学习", "培训", "", 25),
    ("网课", "教育学习", "课程", "", 25),
    ("课程", "教育学习", "课程", "", 30),
    ("考试", "教育学习", "考试报名", "", 25),
    ("报名费", "教育学习", "考试报名", "", 25),
    ("文具", "教育学习", "文具", "", 30),
    ("打印", "教育学习", "打印复印", "", 30),
    ("知网", "教育学习", "文献", "", 30),

    # ---------- 人情 ----------
    ("红包", "人情往来", "红包", "", 30),
    ("转账", "人情往来", "转账", "", 60),
    ("礼物", "人情往来", "礼物", "", 35),
    ("礼金", "人情往来", "礼金", "", 25),
    ("份子", "人情往来", "礼金", "", 25),
    ("捐赠", "人情往来", "公益", "", 25),
    ("公益", "人情往来", "公益", "", 25),

    # ---------- 金融 ----------
    ("手续费", "金融", "手续费", "", 30),
    ("服务费", "金融", "手续费", "", 40),
    ("利息", "金融", "利息", "", 40),
    ("保险", "金融", "保险", "", 30),
    ("花呗", "金融", "信用支付", "", 40),
    ("借呗", "金融", "借贷", "", 35),
    ("贷款", "金融", "借贷", "", 30),
    ("分期", "金融", "信用支付", "", 35),
    ("税费", "金融", "税费", "", 30),
    ("罚款", "金融", "罚款", "", 25),

    # ---------- 补充：生活常见商户 ----------
    ("健身", "娱乐", "运动健身", "", 24),
    ("私教", "娱乐", "运动健身", "", 24),
    ("体育馆", "娱乐", "运动健身", "", 28),
    ("台球", "娱乐", "休闲娱乐", "", 28),
    ("网咖", "娱乐", "休闲娱乐", "", 28),
    ("网吧", "娱乐", "休闲娱乐", "", 28),
    ("足疗", "娱乐", "休闲娱乐", "", 28),
    ("按摩", "娱乐", "休闲娱乐", "", 28),
    ("理发", "购物", "美妆个护", "", 28),
    ("美发", "购物", "美妆个护", "", 28),
    ("美容", "购物", "美妆个护", "", 28),
    ("宠物", "日用百货", "宠物", "", 28),
    ("猫粮", "日用百货", "宠物", "", 28),
    ("狗粮", "日用百货", "宠物", "", 28),
    ("快递", "日用百货", "快递物流", "", 28),
    ("菜鸟", "日用百货", "快递物流", "", 28),
    ("顺丰", "日用百货", "快递物流", "", 28),
    ("洗衣", "日用百货", "生活服务", "", 28),
    ("干洗", "日用百货", "生活服务", "", 28),
    ("照相", "日用百货", "生活服务", "", 30),
    ("摄影", "娱乐", "摄影", "", 30),
    ("鲜花", "人情往来", "礼物", "", 28),
    ("蛋糕店", "餐饮", "烘焙", "", 26),
    ("西饼", "餐饮", "烘焙", "", 26),
    ("包子", "餐饮", "堂食", "", 30),
    ("粥", "餐饮", "堂食", "", 30),
    ("烤肉店", "餐饮", "聚餐", "", 26),
    ("川菜", "餐饮", "堂食", "", 30),
    ("湘菜", "餐饮", "堂食", "", 30),
    ("粤菜", "餐饮", "堂食", "", 30),
    ("日料", "餐饮", "堂食", "", 30),
    ("自助", "餐饮", "聚餐", "", 28),
    ("咖啡店", "餐饮", "咖啡饮品", "", 26),
    ("书店", "教育学习", "书籍", "", 26),
    ("文具店", "教育学习", "文具", "", 26),
    ("眼镜店", "医疗健康", "视力", "", 26),
    ("口腔门诊", "医疗健康", "牙科", "", 26),
    ("宠物医院", "医疗健康", "宠物医疗", "", 26),
    ("洗浴", "居住", "生活服务", "", 30),
    ("燃气费", "居住", "水电燃气", "", 22),
    ("暖气", "居住", "水电燃气", "", 24),
    ("有线电视", "通讯", "宽带", "", 26),
    ("会员费", "娱乐", "会员服务", "", 55),
    ("续费", "娱乐", "会员服务", "", 55),
    ("自动续费", "娱乐", "会员服务", "", 40),
]

NEUTRAL_RE = re.compile("|".join(re.escape(k) for k in NEUTRAL_KEYWORDS), re.IGNORECASE)

RULES_VERSION = 3

# 条件式内置规则（优先级数字大 = 排在具体商户规则之后，只做兜底细分）
# 参考 double-entry-generator 的写法：泛规则在前、具体规则在后，用时间/金额把粗分类再拆细。
DEFAULT_CONDITION_RULES = [
    # 餐饮按用餐时段细分：只在没有更具体的商户规则（外卖/快餐/咖啡…）命中时才生效
    {"keyword": "餐饮", "field": "category", "time_from": "05:00", "time_to": "10:30",
     "category": "餐饮", "sub_category": "早餐", "priority": 45, "note": "按用餐时段归类"},
    {"keyword": "餐饮", "field": "category", "time_from": "10:30", "time_to": "14:30",
     "category": "餐饮", "sub_category": "午餐", "priority": 45, "note": "按用餐时段归类"},
    {"keyword": "餐饮", "field": "category", "time_from": "16:30", "time_to": "21:30",
     "category": "餐饮", "sub_category": "晚餐", "priority": 45, "note": "按用餐时段归类"},
    {"keyword": "餐饮", "field": "category", "time_from": "21:30", "time_to": "05:00",
     "category": "餐饮", "sub_category": "夜宵", "priority": 45, "note": "按用餐时段归类"},
    # 小额支出打标签，月末能看清「零钱都去哪了」
    {"direction": "out", "max_cents": 500, "action": "tag", "tags": "小额", "priority": 95,
     "note": "5 元以内的支出打个「小额」标签"},
    # 大额支出也打个标签，方便回看
    {"direction": "out", "min_cents": 100000, "action": "tag", "tags": "大额", "priority": 96,
     "note": "1000 元以上的支出打个「大额」标签"},
]

FIELD_LABEL = {"any": "商户或商品", "peer": "交易对方", "item": "商品说明",
               "category": "交易分类", "method": "支付方式"}
# 规则里的字段名 -> 判断上下文里的键名
FIELD_TO_CTX = {"any": None, "peer": "peer", "item": "item", "category": "category_raw", "method": "method"}
ACTION_LABEL = {"categorize": "归到某个分类", "neutral": "标记为不计收支",
                "ignore": "直接忽略不入账", "tag": "只打标签"}


def seed_default_rules(store):
    """写入内置规则库；内置规则版本升级时自动重建（用户自己加的规则不受影响）。"""
    version = store.get_state("rules_version")
    if version == RULES_VERSION:
        return 0
    if not version:
        store.mark_legacy_rules_builtin()   # 老库升级：原有规则都是内置的
    store.delete_rules_builtin()
    n = 0
    for kw, cat, sub, direction, prio in DEFAULT_RULES:
        store.add_rule(kw, cat, sub, prio, direction, builtin=True)
        n += 1
    for rule in DEFAULT_CONDITION_RULES:
        store.add_rule(builtin=True, **rule)
        n += 1
    store.set_state("rules_version", RULES_VERSION)
    return n


def in_time_window(hhmm, start, end):
    """判断 HH:MM 是否落在 [start, end) 内；支持跨零点（22:00-02:00）。"""
    if not start or not end or not hhmm:
        return True
    if start <= end:
        return start <= hhmm < end
    return hhmm >= start or hhmm < end          # 跨零点


def rule_context(record):
    """把一条流水摊平成规则判断需要的字段。"""
    cents = abs(int(record.get("amount_cents") or 0))
    ts = str(record.get("ts") or "")
    return {
        "peer": util.norm_text(record.get("counterparty", "")),
        "item": util.norm_text(record.get("item", "")),
        "category_raw": util.norm_text(record.get("category_raw", "")),
        "method": util.norm_text(record.get("method", "")),
        "raw": util.norm_text(str(record.get("raw", ""))[:300]),
        "direction": record.get("direction") or "",
        "source": record.get("source") or "",
        "cents": cents,
        "hhmm": ts[11:16] if len(ts) >= 16 else "",
    }


def rule_matches(rule, ctx):
    """一条规则的全部条件是否都满足。"""
    if rule.get("direction") and rule["direction"] != ctx["direction"]:
        return False
    if rule.get("source") and rule["source"] != ctx["source"]:
        return False
    lo, hi = rule.get("min_cents"), rule.get("max_cents")
    if lo is not None and ctx["cents"] < lo:
        return False
    if hi is not None and ctx["cents"] > hi:
        return False
    if rule.get("method") and util.norm_text(rule["method"]) not in ctx["method"]:
        return False
    if rule.get("time_from") and not in_time_window(ctx["hhmm"], rule["time_from"], rule["time_to"] or "23:59"):
        return False

    field = rule.get("field") or "any"
    ctx_key = FIELD_TO_CTX.get(field, None)
    if ctx_key is None:
        hay = " ".join((ctx["peer"], ctx["item"], ctx["category_raw"], ctx["raw"]))
    else:
        hay = ctx.get(ctx_key, "")
    keywords = [k.strip() for k in re.split(r"[,，、;；]", str(rule.get("keyword") or "")) if k.strip()]
    if not keywords:
        # 没有关键词时，必须至少有一个其它条件，否则会误吞所有交易
        return bool(lo is not None or hi is not None or rule.get("method") or rule.get("time_from"))
    for kw in keywords:
        nk = util.norm_text(kw)
        if not nk:
            continue
        if rule.get("full_match"):
            if hay == nk:
                return True
        elif nk in hay:
            return True
    return False


def evaluate(store, record, rules=None):
    """对一条流水跑一遍规则，返回 {"action","category","sub_category","tags","rule_id","rule"}。

    规则按优先级从小到大依次判断，第一条「归类 / 不计收支 / 忽略」规则生效；
    「打标签」规则可以叠加多条，不影响主判定。
    """
    ctx = rule_context(record)
    rules = store.list_rules(enabled_only=True) if rules is None else rules
    result = {"action": "", "category": "", "sub_category": "", "tags": [], "rule_id": None, "rule": None}
    for rule in rules:
        if not rule_matches(rule, ctx):
            continue
        action = rule.get("action") or "categorize"
        if action == "tag":
            for t in re.split(r"[,，、;；]", str(rule.get("tags") or "")):
                if t.strip() and t.strip() not in result["tags"]:
                    result["tags"].append(t.strip())
            if result["rule_id"] is None and not result["action"]:
                result["tags_rule_id"] = rule["id"]
            continue
        if result["action"]:
            continue
        result["action"] = action
        result["category"] = rule.get("category") or ""
        result["sub_category"] = rule.get("sub_category") or ""
        result["rule_id"] = rule["id"]
        result["rule"] = rule
        if action == "ignore":
            break
    return result


def should_neutralize(record):
    """判断是否为内部资金流转（转账/还款/理财），应不计入收支。"""
    blob = "%s %s %s" % (record.get("category_raw", ""), record.get("counterparty", ""),
                         record.get("item", ""))
    if NEUTRAL_RE.search(util.norm_text(blob) or blob):
        return True
    cat = util.norm_text(record.get("category_raw", ""))
    return cat in ("转账", "信用卡还款", "余额宝", "理财", "零钱提现", "零钱充值")


def classify(store, record, rules=None):
    """给一条流水确定分类，返回 (category, sub_category)。保留此函数以兼容旧调用。"""
    res = evaluate(store, record, rules)
    if res["action"] in ("categorize", "neutral") and res["category"]:
        if res["rule_id"]:
            store.bump_rule(res["rule_id"])
        return res["category"], res["sub_category"]
    direction = record.get("direction")
    if direction == "neutral":
        return "金融", "资金流转"
    if direction == "in":
        return "收入", "其他收入"
    return UNCATEGORIZED, ""


def apply_rules(store, record, rules=None):
    """按规则加工一条流水（就地修改 record），返回规则判定结果。

    - categorize: 填分类
    - neutral:    方向改成不计收支，并填分类
    - ignore:     调用方应丢弃这条流水
    - tag:        往 tags 上叠加标签
    """
    res = evaluate(store, record, rules)
    if res["action"] == "ignore":
        return res
    if res["action"] == "neutral":
        record["direction"] = "neutral"
    if res["action"] in ("categorize", "neutral") and res["category"]:
        record["category"] = res["category"]
        record["sub_category"] = res["sub_category"]
        if res["rule_id"]:
            store.bump_rule(res["rule_id"])
    if res["tags"]:
        old = [t for t in str(record.get("tags") or "").split(",") if t]
        record["tags"] = ",".join(old + [t for t in res["tags"] if t not in old])
    return res


def autocategorize_pending(store, limit=500):
    """把仍然是「未分类」的流水重新分类；顺带补标签。返回更新条数。"""
    rows, _ = store.query(uncategorized=True, limit=limit, order="desc")
    rules = store.list_rules(enabled_only=True)
    n = 0
    for row in rows:
        res = evaluate(store, row, rules)
        cat, sub = classify(store, row, rules)
        fields = {}
        if cat and cat != UNCATEGORIZED:
            fields["category"] = cat
            fields["sub_category"] = sub
        if res["tags"]:
            old = [t for t in str(row.get("tags") or "").split(",") if t]
            merged = ",".join(old + [t for t in res["tags"] if t not in old])
            if merged != (row.get("tags") or ""):
                fields["tags"] = merged
        if fields:
            store.update_tx(row["id"], **fields)
            n += 1
    return n


def learn_from_edit(store, record, category, sub_category=""):
    """用户手工改了分类 -> 记住这个商户，下次自动归类。"""
    kw = (record.get("counterparty") or "").strip()
    if not kw or len(kw) < 2 or len(kw) > 20:
        kw = (record.get("item") or "").strip()[:20]
    if not kw or len(kw) < 2:
        return None
    if kw.isdigit():
        return None
    store.add_rule(kw, category, sub_category, priority=5, field="peer", note="从手工改分类学习")
    return kw

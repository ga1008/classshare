import json
import sqlite3
import sys

from .connection import get_db_connection


def init_default_exam_paper():
    """初始化默认试卷：计算机网络期中测试 (来自 MID.html)"""
    default_exam_id = "mid-computer-network-2024"
    try:
        conn = get_db_connection()
        try:
            existing = conn.execute("SELECT id FROM exam_papers WHERE id = ?", (default_exam_id,)).fetchone()
            if existing:
                return  # 已存在，跳过

            # 获取第一个教师作为默认创建者
            teacher = conn.execute("SELECT id FROM teachers LIMIT 1").fetchone()
            if not teacher:
                print("[DB] 无教师账户，跳过默认试卷初始化。")
                return

            questions_json = json.dumps({
                "pages": [
                    {
                        "name": "第一关·宿舍的网络通了",
                        "questions": [
                            {"id": "q1", "type": "radio", "text": "1. 室友问：“100兆宽带怎么下载只有11.2MB/s？” 正确解释是？", "options": ["网线坏了", "100Mbps = 11.2MB/s左右，单位不同", "迅雷限速", "高峰期拥堵"]},
                            {"id": "q2", "type": "radio", "text": "2. 教务系统卡顿，ping延迟15ms，主要卡顿原因可能是？", "options": ["网线被咬断", "教务系统服务器处理时延大", "CPU不够好", "电磁波变慢"]},
                            {"id": "q3", "type": "radio", "text": "3. 网络分层最主要好处比喻正确的是？", "options": ["食堂打饭排队", "快递公司各司其职，换货车不影响寄件人", "高速车道越多越快", "对讲机轮流说"]},
                            {"id": "q4", "type": "radio", "text": "4. 手机开热点给电脑上网，手机在网络架构中的角色？", "options": ["只属于边缘部分", "只属于核心部分", "同时属于边缘和核心", "无线网络特殊"]},
                            {"id": "q5", "type": "radio", "text": "5. ping百度请求超时，以下说法正确的是？", "options": ["百度服务器宕机", "网线断了", "百度可能禁ping", "IP被拉黑"]},
                            {"id": "q6", "type": "radio", "text": "6. 发送时延取决于数据块长度和带宽。", "options": ["正确", "错误"]},
                            {"id": "q7", "type": "radio", "text": "7. 传播时延只受物理距离影响，与带宽无关。", "options": ["正确", "错误"]},
                            {"id": "q8", "type": "radio", "text": "8. 排队时延可能是四个时延中唯一可能为零的时延。", "options": ["正确", "错误"]},
                            {"id": "q9", "type": "radio", "text": "9. tracert某行全是* * * 表示那个路由器肯定坏了。", "options": ["正确", "错误"]},
                            {"id": "q10", "type": "radio", "text": "10. “透明传输”是指数据完全可见无加密。", "options": ["正确", "错误"]}
                        ]
                    },
                    {
                        "name": "第二关·信号里的秘密",
                        "questions": [
                            {"id": "q11_1", "type": "textarea", "text": "11.(1) KTV包厢噪声大，为了让对方听清，可以采取哪两种策略？分别对应香农公式中的哪个变量？", "placeholder": "例如：提高信号功率/降低速率..."},
                            {"id": "q11_2", "type": "textarea", "text": "11.(2) 噪声N趋近0时，信道容量会怎样变化？为什么不能无限大？", "placeholder": ""},
                            {"id": "q12_1", "type": "textarea", "text": "12.(1) 为什么大多数办公室仍用双绞线而非光纤？（写出2个理由）", "placeholder": ""},
                            {"id": "q12_2", "type": "text", "text": "12.(2) 食堂窗口轮流打饭5分钟，属于哪种复用技术？", "placeholder": ""},
                            {"id": "q13_1", "type": "textarea", "text": "13.(1) 手电筒狂闪1000次看到常亮，物理信道存在什么现象？", "placeholder": ""},
                            {"id": "q13_2", "type": "textarea", "text": "13.(2) 对应哪个著名定律？核心结论是什么？", "placeholder": ""}
                        ]
                    },
                    {
                        "name": "第三关·丢包的心跳",
                        "questions": [
                            {"id": "q14_1", "type": "textarea", "text": "14.(1) 数据包传输中，IP地址和MAC地址分别由谁负责“导航”和“送货”？", "placeholder": ""},
                            {"id": "q14_2", "type": "textarea", "text": "14.(2) 为什么需要同时存在IP和MAC地址？只用其中一个不行吗？", "placeholder": ""},
                            {"id": "q15_1", "type": "textarea", "text": "15.(1) 电脑发出什么请求获取MAC？该协议名称？", "placeholder": ""},
                            {"id": "q15_2", "type": "textarea", "text": "15.(2) 坏同学想偷听通信可以伪造什么攻击？叫什么？", "placeholder": ""},
                            {"id": "q15_3", "type": "textarea", "text": "15.(3) ARP缓存表为什么不永久保存？", "placeholder": ""},
                            {"id": "q16_1", "type": "textarea", "text": "16.(1) 以太网用什么协议解决“谁先说话”？用一句话描述核心规则。", "placeholder": ""},
                            {"id": "q16_2", "type": "textarea", "text": "16.(2) 两台电脑同时发送数据会发生什么？", "placeholder": ""},
                            {"id": "q16_3", "type": "textarea", "text": "16.(3) 为什么以太网规定最短帧长64字节？不遵守会怎样？", "placeholder": ""},
                            {"id": "q17_1", "type": "text", "text": "17.(1) 数据M=1011，生成多项式10011，计算FCS和最终完整比特流。", "placeholder": "例如：余数xxxx，最终帧："},
                            {"id": "q17_2", "type": "textarea", "text": "17.(2) 接收端收到10111110余数为0，说明什么？", "placeholder": ""}
                        ]
                    },
                    {
                        "name": "第四关·宿管大妈的账本",
                        "questions": [
                            {"id": "q18_1", "type": "text", "text": "18.(1) 网段192.168.10.64/26 的子网掩码是多少？", "placeholder": "例如255.255.255.192"},
                            {"id": "q18_2", "type": "text", "text": "18.(2) 该网段的广播地址？", "placeholder": ""},
                            {"id": "q18_3", "type": "text", "text": "18.(3) 可用IP范围？", "placeholder": ""},
                            {"id": "q18_4", "type": "text", "text": "18.(4) 最多能连多少台设备？", "placeholder": ""},
                            {"id": "q19_1", "type": "textarea", "text": "19.(1) 路由表匹配: 目的IP 10.1.1.5, 10.1.2.3, 8.8.8.8分别从哪个接口转发？", "placeholder": ""},
                            {"id": "q19_2", "type": "textarea", "text": "19.(2) 判断依据是什么原则？", "placeholder": ""},
                            {"id": "q19_3", "type": "textarea", "text": "19.(3) 0.0.0.0/0路由叫什么？作用？", "placeholder": ""},
                            {"id": "q20_1", "type": "textarea", "text": "20.(1) 离开电脑时，源IP:端口 目的IP:端口？", "placeholder": ""},
                            {"id": "q20_2", "type": "textarea", "text": "20.(2) NAT映射表新增记录？", "placeholder": ""},
                            {"id": "q20_3", "type": "textarea", "text": "20.(3) 服务器回复时目的IP端口？路由器如何找到内网主机？", "placeholder": ""}
                        ]
                    },
                    {
                        "name": "极客进阶·附加挑战",
                        "questions": [
                            {"id": "add1", "type": "textarea", "text": "附加题1：tracert第6跳超时但后续正常，路由器真的宕机了吗？为什么“沉默”？tracert如何利用TTL发现路由？", "placeholder": ""},
                            {"id": "add2", "type": "textarea", "text": "附加题2：RIP与OSPF选路场景：A-B高速，B-C低速，RIP如何选路？OSPF如何选？为什么大厂抛弃RIP？", "placeholder": ""}
                        ]
                    }
                ]
            }, ensure_ascii=False)

            conn.execute(
                "INSERT OR IGNORE INTO exam_papers (id, teacher_id, title, description, questions_json, status) VALUES (?, ?, ?, ?, ?, ?)",
                (default_exam_id, teacher['id'], "计算机网络·期中测试 — 连接时光的故事",
                 "基于MID.html的计算机网络期中测试试卷，包含网络分层、时延、香农定理、IP/MAC、ARP、以太网、CIDR、路由、NAT等知识点。",
                 questions_json, 'ready')
            )
            conn.commit()
            print("[DB] 默认试卷「计算机网络·期中测试」初始化完成。")
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    except Exception as e:
        print(f"[DB WARN] 初始化默认试卷失败: {e}")

import requests
import openai
import plugins
from plugins import *
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from datetime import datetime, timedelta

# 配置文件内容
open_ai_api_key = "填写你自己的openai key"
model = "gpt-3.5-turbo"
open_ai_api_base = "https://api.openai.com/v1"

BASE_URL_HIGHSPEEDTICKET = "https://api.pearktrue.cn/api/highspeedticket"

@plugins.register(name="TicketQuery",
                  desc="票务查询插件",
                  version="1.0",
                  author="Cool",
                  desire_priority=100)
class TicketQuery(Plugin):
    content = None
    ticket_info_list = []
    intermediate_ticket_info_list = []  # 保存中转信息
    conversation_history = []  # 保存会话历史
    last_interaction_time = None

    def __init__(self):
        super().__init__()
        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        logger.info(f"[{__class__.__name__}] inited")

    def get_help_text(self, **kwargs):
        help_text = f"发送【票种 出发地 终点地 查询日期 查询时间】获取票务信息\n" \
                    f"例如：【高铁 北京 上海 2024-06-05 09:00】\n" \
                    f"发送【中转+票种 中转地 终点地 查询日期 查询时间】进行中转查询\n" \
                    f"发送【+问题】继续筛选车次信息"
        return help_text

    def on_handle_context(self, e_context: EventContext):
        if e_context['context'].type != ContextType.TEXT:
            return
        self.content = e_context["context"].content.strip()

        # 检查是否超过10分钟，如果超过则清除对话历史
        if self.last_interaction_time and datetime.now() - self.last_interaction_time > timedelta(minutes=10):
            self.conversation_history.clear()
            self.ticket_info_list.clear()
            self.intermediate_ticket_info_list.clear()

        self.last_interaction_time = datetime.now()

        if self.content.startswith("+"):
            question = self.content[1:].strip()
            logger.info(f"[{__class__.__name__}] 收到筛选问题: {question}")
            self.conversation_history.append({"role": "user", "content": question})
            reply = Reply()
            result = self.filter_with_openai(question)
            if result:
                result = self.ensure_single_prompt(result)
                self.conversation_history.append({"role": "assistant", "content": result})
                reply.type = ReplyType.TEXT
                reply.content = result
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
            return

        if self.content.startswith("中转+"):
            logger.info(f"[{__class__.__name__}] 收到中转查询消息: {self.content}")
            parts = self.content[3:].split()
            if len(parts) < 5:
                reply = Reply()
                reply.type = ReplyType.ERROR
                reply.content = "格式错误，请发送：中转+票种 中转地 终点地 查询日期 查询时间"
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return

            ticket_type = parts[0]
            intermediate_location = parts[1]
            to_location = parts[2]
            query_date = parts[3]
            query_time = parts[4]

            self.conversation_history.append({"role": "user", "content": self.content})
            reply = Reply()
            result = self.get_ticket_info(ticket_type, intermediate_location, to_location, query_date, query_time, intermediate=True)
            if result:
                result = self.ensure_single_prompt(result)
                self.conversation_history.append({"role": "assistant", "content": result})
                reply.type = ReplyType.TEXT
                reply.content = result
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
            else:
                error_message = f"当前{ticket_type}没有符合当前条件的车次，请选择其他票种/时间。"
                error_message = self.ensure_single_prompt(error_message)
                self.conversation_history.append({"role": "assistant", "content": error_message})
                reply.type = ReplyType.ERROR
                reply.content = error_message
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
            return

        if self.content.split()[0] in ["高铁", "普通", "动车"]:
            logger.info(f"[{__class__.__name__}] 收到消息: {self.content}")
            parts = self.content.split()
            if len(parts) < 5:
                reply = Reply()
                reply.type = ReplyType.ERROR
                reply.content = "格式错误，请发送：票种 出发地 终点地 查询日期 查询时间"
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return

            ticket_type = parts[0]
            from_location = parts[1]
            to_location = parts[2]
            query_date = parts[3]
            query_time = parts[4]

            self.conversation_history.append({"role": "user", "content": self.content})
            reply = Reply()
            result = self.get_ticket_info(ticket_type, from_location, to_location, query_date, query_time)
            if result:
                result = self.ensure_single_prompt(result)
                self.conversation_history.append({"role": "assistant", "content": result})
                reply.type = ReplyType.TEXT
                reply.content = result
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
            else:
                error_message = f"当前{ticket_type}没有符合当前条件的车次，请选择其他票种/时间。"
                error_message = self.ensure_single_prompt(error_message)
                self.conversation_history.append({"role": "assistant", "content": error_message})
                reply.type = ReplyType.ERROR
                reply.content = error_message
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS

    def get_ticket_info(self, ticket_type, from_location, to_location, query_date, query_time, intermediate=False):
        params = {"from": from_location, "to": to_location, "time": query_date}
        headers = {'Content-Type': "application/x-www-form-urlencoded"}
        try:
            response = requests.get(url=BASE_URL_HIGHSPEEDTICKET, params=params, headers=headers, timeout=2)
            if response.status_code == 200:
                json_data = response.json()
                logger.info(f"接口返回的数据：{json_data}")
                if json_data.get('code') == 200 and json_data.get('data'):
                    data = json_data['data']
                    filtered_tickets = self.filter_tickets(data, query_time, ticket_type)
                    if not filtered_tickets:
                        return None
                    if intermediate:
                        self.intermediate_ticket_info_list = filtered_tickets  # 保存中转车次信息
                    else:
                        self.ticket_info_list = filtered_tickets  # 保存车次信息以供后续筛选
                    ticket_info_list = []
                    for ticket in filtered_tickets[:5]:
                        ticket_info = (
                            f"车辆类型: {ticket.get('traintype', 'N/A')}\n"
                            f"车辆代码: {ticket.get('trainumber', 'N/A')}\n"
                            f"出发点: {ticket.get('departstation', 'N/A')}\n"
                            f"终点站: {ticket.get('arrivestation', 'N/A')}\n"
                            f"出发时间: {ticket.get('departtime', 'N/A')}\n"
                            f"到达时间: {ticket.get('arrivetime', 'N/A')}\n"
                            f"过程时间: {ticket.get('runtime', 'N/A')}\n"
                        )
                        for seat in ticket.get('ticket_info', []):
                            seat_info = (
                                f"座次等级: {seat.get('seatname', 'N/A')}\n"
                                f"车票状态: {seat.get('bookable', 'N/A')}\n"
                                f"车票价格: {seat.get('seatprice', 'N/A')}元\n"
                                f"剩余车票数量: {seat.get('seatinventory', 'N/A')}\n"
                            )
                            ticket_info += seat_info
                        ticket_info_list.append(ticket_info)
                    return "\n\n".join(ticket_info_list)
                else:
                    logger.error(f"主接口返回值异常: {json_data}")
                    raise ValueError('not found')
            else:
                logger.error(f"主接口请求失败: {response.text}")
                raise Exception('request failed')
        except Exception as e:
            logger.error(f"接口异常：{e}")
        logger.error("所有接口都挂了,无法获取")
        return None

    def filter_tickets(self, data, query_time, ticket_type):
        query_datetime = datetime.strptime(query_time, "%H:%M").time()
        filtered_tickets = []
        for ticket in data:
            depart_time = datetime.strptime(ticket.get('departtime', '00:00'), "%H:%M").time()
            if depart_time >= query_datetime and ticket.get('traintype', '').lower() == ticket_type.lower():
                filtered_tickets.append(ticket)
        return filtered_tickets

    def filter_with_openai(self, question):
        openai.api_key = open_ai_api_key
        openai.api_base = open_ai_api_base

        tickets_info = "\n".join(self.format_ticket_info(self.ticket_info_list))
        intermediate_tickets_info = "\n".join(self.format_ticket_info(self.intermediate_ticket_info_list))

        conversation = self.conversation_history.copy()
        conversation.append({"role": "system", "content": f"车次信息:\n{tickets_info}\n中转车次信息:\n{intermediate_tickets_info}"})

        try:
            response = openai.ChatCompletion.create(
                model=model,
                messages=conversation
            )
            reply_content = response.choices[0].message['content']
            return reply_content
        except Exception as e:
            logger.error(f"OpenAI API 调用异常：{e}")
            return "对不起，处理请求时出现错误，请稍后再试。"

    def format_ticket_info(self, tickets):
        formatted_info = []
        for ticket in tickets:
            ticket_info = (
                f"车辆类型: {ticket.get('traintype', 'N/A')}\n"
                f"车辆代码: {ticket.get('trainumber', 'N/A')}\n"
                f"出发点: {ticket.get('departstation', 'N/A')}\n"
                f"终点站: {ticket.get('arrivestation', 'N/A')}\n"
                f"出发时间: {ticket.get('departtime', 'N/A')}\n"
                f"到达时间: {ticket.get('arrivetime', 'N/A')}\n"
                f"过程时间: {ticket.get('runtime', 'N/A')}\n"
            )
            for seat in ticket.get('ticket_info', []):
                seat_info = (
                    f"座次等级: {seat.get('seatname', 'N/A')}\n"
                    f"车票状态: {seat.get('bookable', 'N/A')}\n"
                    f"车票价格: {seat.get('seatprice', 'N/A')}元\n"
                    f"剩余车票数量: {seat.get('seatinventory', 'N/A')}\n"
                )
                ticket_info += seat_info
            formatted_info.append(ticket_info)
        return formatted_info

    def ensure_single_prompt(self, response):
        if "+问题" not in response:
            return response + "\n\n【+问题】可继续筛选车次信息，10分钟内可继续对话。"
        return response

if __name__ == "__main__":
    ticket_query_plugin = TicketQuery()
    result = ticket_query_plugin.get_ticket_info("高铁", "北京", "上海", "2024-06-05", "09:00")
    if result:
        print("获取到的票务信息：", result)
    else:
        print("获取失败")

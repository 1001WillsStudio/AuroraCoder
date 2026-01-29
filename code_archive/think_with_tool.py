from openai import OpenAI
from transformers import AutoTokenizer
from src.string_processor import process_string

client = OpenAI(base_url='http://127.0.0.1:8080/v1', api_key='any')
# Year-Month: {date.today().strftime('%Y-%m')}


messages = [
    {"role": "system",
     "content": f"""You are a helpful assistant who has powerful tools to use. Don't rely on ur internal knowledge too much, since ur training data is out of date by years.

# Tools
1. Python, input a string of Python code, the result of will be provided. The python is working on live environment.
By using <python>string</python>, the string will be executed and give you result. 

2. Google search: a tool to search on google, and provide the result in text.
By using <search>query</search>, google search result will be provided.

3. Web Browser
By using <browser>url</browser>, The content in the website will be provided.

You can use these tools at anytime, during thinking (inside <think> </think>), and as many times as you like.
These tools are for internal usage only, don't display any tool call to user. 
Note that the tool must be used in XML format, like <abc>xxx</abc>."""}
    ,
    {
        "role": "user",
        "content": "帮我推荐一些关于'单拉链无卡槽女士钱包'的商品",
    }
]
# 上次nvidia gtc结束多久了？
# 帮我推荐一些关于'单拉链无卡槽女士钱包'的商品
# 帮我找台能在最高画质下以100+fps玩4k 黑神话的电脑。
# 2025年618有什么值得买的东西？
# 当前是2025年，组装一个配置拉满的PC要多少钱？请先对所有部件进行详尽的分析，并提供所有部件具体商品的购买链接。
# 帮我推荐一些关于'适合尿酸高人群的啤酒'的商品
# 现在各个款式的RTX 5080型号到底有什么区别，哪个性价比最高？
tokenizer = AutoTokenizer.from_pretrained("Qwen/QwQ-32B")
text = tokenizer.apply_chat_template(messages, tokenize=False)
prompt_text = text + "<|im_start|>assistant\n<think>\n"
print(prompt_text)

while True:
    current_completion = ""
    print("Request Sent")
    completion = client.completions.create(model="Qwen/QwQ-32B", prompt=prompt_text,
                                           stop=['</python>', '</search>', '</browser>',
                                                 '</python_result>', '</search_result>',
                                                 '</browser_result>'],
                                           max_tokens=8192,
                                           stream=True)
    for chunk in completion:
        # print(chunk)
        content = chunk.choices[0].text
        if content:
            print(content, end='')
            current_completion += content
    print("Response Received")
    processed_completion = process_string(current_completion)
    print(current_completion, processed_completion)
    if current_completion == processed_completion and current_completion:
        print(f"Final Completion: \n{prompt_text + processed_completion}")
        break
    else:
        print("-----------------------------------------------------------------------")
        prompt_text += processed_completion
        print(prompt_text)

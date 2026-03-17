from openai import OpenAI
from transformers import AutoTokenizer
from datetime import date
from string_processer import process_string

client= OpenAI(base_url='http://localhost:8000/v1', api_key='any')
# Year-Month: {date.today().strftime('%Y-%m')}
messages = [
        {"role":"system",
         "content":f"""You are a helpful assistant who has a powerful tool to use. Assume user's questions are valid and meaningful.
Don't rely on ur internal knowledge too much, since ur training data is out of date by years.-

## Tools
1. Python, input a string of Python code, the result of will be provided. The python is working on live environment.
By using <python>string</python>, the string will be executed and give you result in format <python_result>result</python_result>. 

2. Google search: a tool to search on google, and provide the result in text.
By using <search>query</search>, google search result will be provided in format <search_result>result</search_result>.

3. Web Browser
By using <browser>url</browser>, The content in the website will be provided in format <browser_result>result</browser_result>.

You can use these tools at anytime, inside/outside <think> </think>, and as many times as you like.
If there is anything that you are not sure, check with ur tools.
Note: python/search/browser will not be displayed to user."""}
        ,
        {
            "role": "user",
            "content": """A digital display shows the current date as an $8$-digit integer consisting of a $4$-digit year, followed by a $2$-digit month, followed by a $2$-digit date within the month. For example, Valentine's Day 2023 is displayed as $20230214$. For how many dates in $2023$ will each digit appear an even number of times in the 8-digital display for that date? $\\textbf{(A)}~9\\qquad\\textbf{(B)}~7\\qquad\\textbf{(C)}~5\\qquad\\textbf{(D)}~6\\qquad\\textbf{(E)}~8$ If you cannot determine the correct multiple-choice answer, take your best guess. Once you have your answer, please duplicate that letter five times in a single string. For example, if the answer is F, then write FFFFF.""",
        }
]

tokenizer = AutoTokenizer.from_pretrained("Qwen/QwQ-32B")
text=tokenizer.apply_chat_template(messages,tokenize=False)
prompt_text= text+"<|im_start|>assistant\n<think>\n"
print(prompt_text)

# chat_completion = client.chat.completions.create(
#     messages=messages,
#     model="Qwen",
#     stream=True,
#     stop=['<|end_of_text|>','<|eot_id|>','</tool_call>'],
# )
#
#
#
# for chunk in chat_completion:
#     content=chunk.choices[0].delta.content
#     if content:
#         print(chunk.choices[0].delta.content,end='')

while True:
    current_completion=""
    completion = client.completions.create(model="qwen", prompt=prompt_text,stop=['</python>','</search>','</browser>'], max_tokens=16384, stream=True)
    for chunk in completion:
        # print(chunk)
        content=chunk.choices[0].text
        if content:
            print(content,end='')
            current_completion+=content
    processed_completion= process_string(current_completion)
    if current_completion == processed_completion:
        break
    else:
        print("-----------------------------------------------------------------------")
        print(processed_completion)
        prompt_text+=processed_completion
from flask import Blueprint, render_template, request, jsonify
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI
import os

text_to_sql_blueprint = Blueprint('text_to_sql', __name__)

@text_to_sql_blueprint.route('/')
def text_to_sql():
    return render_template('text_to_sql.html')

@text_to_sql_blueprint.route('/generate-sql', methods=['POST'])
def generate_sql():
    try:
        data = request.json
        question = data.get('question')
        schema = data.get('schema')
        
        # LangChain setup
        llm = ChatOpenAI(model="gpt-4", temperature=0)
        prompt = ChatPromptTemplate.from_messages([
            ("system", "Convert this question to SQL. Schema:\n{schema}\nOnly return SQL, no explanation."),
            ("human", "{question}")
        ])
        chain = prompt | llm | StrOutputParser()
        
        result = chain.invoke({
            "question": question,
            "schema": schema
        })
        
        return jsonify({"sql": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

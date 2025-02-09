import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import IsolationForest
from sklearn.linear_model import LinearRegression
from statsmodels.tsa.seasonal import seasonal_decompose
import logging
from openai import OpenAI
import re
import plotly.express as px
import plotly.graph_objects as go
from flask.json.provider import DefaultJSONProvider
from flask import Blueprint, request, jsonify, render_template, session, redirect, url_for
import json
import math
from plotly.io import to_json
from sklearn.ensemble import RandomForestClassifier
from plotly.subplots import make_subplots
import altair as alt

logger = logging.getLogger(__name__)

# Custom Exceptions
class AIAnalysisError(Exception):
    """Base exception for AI analysis errors"""
    pass

class DataValidationError(AIAnalysisError):
    """Exception raised for data validation errors"""
    pass

class AIResponseError(AIAnalysisError):
    """Exception raised for AI response generation errors"""
    pass

class CustomJSONProvider(DefaultJSONProvider):
    def default(self, obj):
        try:
            return super().default(obj)
        except Exception as error:
            return str(obj)

class AIAnalyzer:
    def __init__(self, df):
        self.df = df
        self.client = OpenAI()
        
        # Initialize column types
        self.numeric_cols = df.select_dtypes(include=[np.number]).columns
        self.categorical_cols = df.select_dtypes(include=['object']).columns
        self.date_cols = [col for col in df.columns if pd.api.types.is_datetime64_any_dtype(df[col])]
        
        # Generate column descriptions
        column_descriptions = []
        for col in df.columns:
            sample_values = df[col].dropna().head(3).tolist()
            sample_str = ", ".join(str(x) for x in sample_values)
            column_descriptions.append(f"- {col} (e.g., {sample_str})")
        
        dataset_description = self._generate_dataset_description(df)
        
        self.system_prompt = f"""You are an expert Python data analyst analyzing a dataset:
- {len(self.df)} records
- Columns:
{chr(10).join(column_descriptions)}

{dataset_description}

When suggesting analyses:
1. Always format suggestions as numbered choices: "1. **Analysis Title**: Description and context"
2. Limit to 3-5 relevant choices based on the data
3. Make suggestions specific to the available columns
4. Include both visualization and statistical analysis options

When providing analysis code:
1. Start with "=== ANALYSIS CODE BEGIN ==="
2. Include detailed comments explaining each step
3. For visualizations: Use matplotlib and seaborn (not plotly)
4. For tables: Use pandas DataFrame display methods
5. End with "=== ANALYSIS CODE END ==="

Example visualization code:
=== ANALYSIS CODE BEGIN ===
import matplotlib.pyplot as plt
import seaborn as sns

# Create the visualization
plt.figure(figsize=(10, 6))
sns.barplot(data=df, x='category', y='value')
plt.title('Analysis Title')
plt.xlabel('Category')
plt.ylabel('Value')

# Save to bytes for display
from io import BytesIO
buffer = BytesIO()
plt.savefig(buffer, format='png')
buffer.seek(0)
result = buffer.getvalue()
=== ANALYSIS CODE END ===

Example table code:
=== ANALYSIS CODE BEGIN ===
# Create summary table
summary_df = df.describe()
result = summary_df.to_html()
=== ANALYSIS CODE END ===
"""

        self.conversation_history = []
        logger.info("Successfully initialized AIAnalyzer")

    def reset_state(self):
        """Reset conversation history"""
        self.conversation_history = []
        logger.info("Reset conversation history")

    def validate_dataframe(self, df):
        """Validate the input DataFrame"""
        try:
            if not isinstance(df, pd.DataFrame):
                raise ValueError("Input must be a pandas DataFrame")
            if df.empty:
                raise ValueError("DataFrame is empty")
            if not all(df.columns):
                raise ValueError("DataFrame contains unnamed columns")
                
            logger.info(f"Validated DataFrame with shape: {df.shape}")
            return True
            
        except Exception as e:
            logger.error(f"DataFrame validation error: {str(e)}")
            raise AIAnalysisError(f"Invalid DataFrame: {str(e)}")

    def generate_smart_insights(self):
        """Generate comprehensive insights about the data"""
        try:
            insights = {
                'patterns': self.detect_patterns(),
                'anomalies': self.find_anomalies(),
                'trends': self.analyze_trends(),
                'correlations': self.find_significant_correlations(),
                'recommendations': self.suggest_visualizations()
            }
            return insights
        except Exception as e:
            logger.error(f"Error generating insights: {str(e)}")
            return {"error": str(e)}

    def detect_patterns(self):
        """Detect patterns in numeric data"""
        try:
            patterns = {}
            for col in self.numeric_cols:
                patterns[col] = {
                    'mean': float(self.df[col].mean()),
                    'median': float(self.df[col].median()),
                    'std': float(self.df[col].std()),
                    'skew': float(self.df[col].skew())
                }
            logger.info(f"Detected patterns for {len(patterns)} columns")
            return patterns
        except Exception as e:
            logger.error(f"Error in detect_patterns: {str(e)}")
            return {}

    def find_anomalies(self, contamination=0.1):
        """Find anomalies using IsolationForest"""
        try:
            anomalies = {}
            for col in self.numeric_cols:
                data = self.df[col].dropna().values.reshape(-1, 1)
                if len(data) > 0:
                    iso_forest = IsolationForest(contamination=contamination)
                    yhat = iso_forest.fit_predict(data)
                    anomaly_indices = np.where(yhat == -1)[0]
                    anomalies[col] = {
                        'count': len(anomaly_indices),
                        'percentage': (len(anomaly_indices) / len(data)) * 100,
                        'indices': anomaly_indices.tolist()
                    }
            logger.info(f"Found anomalies in {len(anomalies)} columns")
            return anomalies
        except Exception as e:
            logger.error(f"Error in find_anomalies: {str(e)}")
            return {}

    def analyze_trends(self):
        """Analyze trends in time series data"""
        trends = {}
        try:
            for date_col in self.date_cols:
                for num_col in self.numeric_cols:
                    if len(self.df) > 2:
                        temp_df = self.df.sort_values(date_col)
                        x = np.arange(len(temp_df))
                        y = temp_df[num_col].values
                        z = np.polyfit(x, y, 1)
                        trends[f"{num_col}_by_{date_col}"] = {
                            'slope': float(z[0]),
                            'direction': 'increasing' if z[0] > 0 else 'decreasing'
                        }
            return trends
        except Exception as e:
            logger.error(f"Error analyzing trends: {str(e)}")
            return {}

    def find_significant_correlations(self, threshold=0.5):
        """Find correlations above threshold"""
        try:
            correlations = {}
            if len(self.numeric_cols) > 1:
                corr_matrix = self.df[self.numeric_cols].corr()
                for i in range(len(self.numeric_cols)):
                    for j in range(i+1, len(self.numeric_cols)):
                        corr_value = corr_matrix.iloc[i, j]
                        if abs(corr_value) >= threshold:
                            pair = f"{self.numeric_cols[i]} vs {self.numeric_cols[j]}"
                            correlations[pair] = {
                                'correlation': float(corr_value),
                                'strength': 'strong' if abs(corr_value) > 0.7 else 'moderate'
                            }
            logger.info(f"Found {len(correlations)} significant correlations")
            return correlations
        except Exception as e:
            logger.error(f"Error in find_significant_correlations: {str(e)}")
            return {}

    def suggest_visualizations(self):
        """Suggest appropriate visualizations"""
        try:
            suggestions = {
                'time_series': [],
                'correlations': [],
                'distributions': [],
                'comparisons': []
            }
            for date_col in self.date_cols:
                for num_col in self.numeric_cols:
                    suggestions['time_series'].append({
                        'type': 'line',
                        'x': date_col,
                        'y': num_col,
                        'title': f'{num_col} over time'
                    })
            if len(self.numeric_cols) >= 2:
                suggestions['correlations'].append({
                    'type': 'heatmap',
                    'columns': self.numeric_cols.tolist(),
                    'title': 'Correlation Matrix'
                })
            for num_col in self.numeric_cols:
                suggestions['distributions'].append({
                    'type': 'histogram',
                    'column': num_col,
                    'title': f'Distribution of {num_col}'
                })
            for cat_col in self.categorical_cols:
                for num_col in self.numeric_cols:
                    suggestions['comparisons'].append({
                        'type': 'box',
                        'x': cat_col,
                        'y': num_col,
                        'title': f'{num_col} by {cat_col}'
                    })
            logger.info("Generated visualization suggestions")
            return suggestions
        except Exception as e:
            logger.error(f"Error in suggest_visualizations: {str(e)}")
            return {'time_series': [], 'correlations': [], 'distributions': [], 'comparisons': []}

    def generate_context_help(self, user_history=None):
        """Generate context-aware help and suggestions"""
        try:
            help_suggestions = {
                'data_quality': [],
                'analysis_suggestions': [],
                'visualization_suggestions': [],
                'advanced_insights': []
            }
            missing_data = self.df.isnull().sum()
            if missing_data.any():
                help_suggestions['data_quality'].append({
                    'type': 'missing_data',
                    'message': f'Found {missing_data.sum()} missing values. Consider cleaning the data.',
                    'affected_columns': missing_data[missing_data > 0].index.tolist()
                })
            if len(self.numeric_cols) > 0:
                help_suggestions['analysis_suggestions'].append({
                    'type': 'statistical',
                    'message': 'You can perform statistical analysis on numeric columns.',
                    'suggested_analyses': ['correlation', 'regression', 'distribution']
                })
            if len(self.date_cols) > 0:
                help_suggestions['analysis_suggestions'].append({
                    'type': 'temporal',
                    'message': 'Time-based analysis available for date columns.',
                    'suggested_analyses': ['trend', 'seasonality', 'forecasting']
                })
            return help_suggestions
        except Exception as e:
            logger.error(f"Error generating context help: {str(e)}")
            return {}

    def generate_smart_response(self, user_message):
        """Generate context-aware responses based on user input"""
        try:
            # Check if user selected an analysis option
            if user_message.strip().isdigit():
                option_num = int(user_message)
                # Get the last AI message from history
                last_suggestions = next((msg['content'] for msg in reversed(self.conversation_history) 
                                      if msg['type'] == 'ai'), None)
                if last_suggestions:
                    return self._handle_analysis_selection(option_num, last_suggestions)
            
            # Generate new suggestions
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_message}
            ]
            
            # Add relevant conversation history
            if self.conversation_history:
                for msg in self.conversation_history[-3:]:
                    role = "assistant" if msg['type'] == 'ai' else "user"
                    messages.append({"role": role, "content": msg['content']})
            
            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=messages,
                temperature=0.2
            )
            
            ai_response = response.choices[0].message.content
            
            # Update conversation history
            self.conversation_history.append({
                'type': 'user',
                'content': user_message,
                'timestamp': pd.Timestamp.now()
            })
            self.conversation_history.append({
                'type': 'ai',
                'content': ai_response,
                'timestamp': pd.Timestamp.now()
            })
            
            return {
                'message': ai_response,
                'type': 'success'
            }
            
        except Exception as e:
            logger.error(f"Error generating response: {str(e)}")
            raise AIResponseError(f"Failed to generate response: {str(e)}")

    def _handle_analysis_selection(self, option_num, last_suggestions):
        """Handle when user selects an analysis option"""
        try:
            # Generate analysis code based on selection
            prompt = f"""Based on the previous suggestions:
{last_suggestions}

Generate the Python code for option {option_num}. 
Include detailed comments explaining the analysis steps."""

            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2
            )
            
            code_response = response.choices[0].message.content
            
            # Extract code between markers
            if "=== ANALYSIS CODE BEGIN ===" in code_response and "=== ANALYSIS CODE END ===" in code_response:
                code = code_response.split("=== ANALYSIS CODE BEGIN ===")[1].split("=== ANALYSIS CODE END ===")[0].strip()
                
                return {
                    'message': f"Here's the Python code for your selected analysis:\n\n```python\n{code}\n```",
                    'type': 'code',
                    'code': code
                }
            
            return {
                'message': "Could not generate valid analysis code. Please try another option.",
                'type': 'error'
            }
            
        except Exception as e:
            logger.error(f"Error handling analysis selection: {str(e)}")
            raise AIResponseError(f"Failed to handle analysis selection: {str(e)}")

    def _generate_dataset_description(self, df):
        """Generate a description of the dataset based on its contents"""
        try:
            description = "The data shows "
            time_cols = [col for col in df.columns if any(term in col.lower() for term in ['year', 'date', 'time', 'period'])]
            main_numeric = [col for col in self.numeric_cols if not any(term in col.lower() for term in ['id', 'index', 'code'])]
            main_categorical = [col for col in self.categorical_cols if not any(term in col.lower() for term in ['id', 'code', 'index'])]
            if main_numeric and main_categorical:
                description += f"{', '.join(main_numeric)} across different {', '.join(main_categorical)}"
            elif main_numeric:
                description += f"{', '.join(main_numeric)}"
            if time_cols:
                description += f" over {time_cols[0].lower()}"
            description += "."
            return description
        except Exception as e:
            logger.error(f"Error generating dataset description: {str(e)}")
            return "The data contains various measurements and categories."

def clean_data(df):
    """Robust cleaning for all data types"""
    # Convert infinities first
    df = df.replace([np.inf, -np.inf], np.nan)
    
    # Handle all NaN types
    df = df.applymap(lambda x: 0 if pd.isna(x) else x)
    
    # Clean string representations
    df.replace(['NaN', 'nan', 'NAN', 'Infinity', '-Infinity'], 0, inplace=True)
    
    # Clean object-type columns
    for col in df.select_dtypes(include='object'):
        df[col] = df[col].apply(lambda x: x if pd.notna(x) else 0)
    
    return df

# Define the blueprint for the upload chat functionality
upload_chat = Blueprint('upload_chat_bp', __name__)

@upload_chat.route('/chat', methods=['GET', 'POST'])
def chat():
    if request.method == 'POST':
        data = request.get_json()
        user_message = data.get('message', '')
        # Retrieve the uploaded dataset stored in session (ensure it was set by your upload logic)
        upload_data = session.get('upload_data')
        if not upload_data:
            return jsonify({'error': 'No uploaded dataset found.'}), 400
        try:
            df = pd.DataFrame(upload_data)
        except Exception as e:
            logger.error(f"Error converting session upload_data to DataFrame: {str(e)}")
            return jsonify({'error': 'Uploaded dataset is invalid.'}), 400
        try:
            analyzer = AIAnalyzer(df)
            response = analyzer.generate_smart_response(user_message)
            return jsonify(response)
        except Exception as e:
            logger.error(f"Error in chat endpoint: {str(e)}")
            return jsonify({'error': str(e)}), 500
    else:
        return render_template('upload_chat.html')

@upload_chat.route('/execute_ai_analysis', methods=['POST'])
def execute_ai_analysis():
    try:
        data = request.get_json()
        analysis_prompt = data.get('analysis_prompt', '')
        option_number = data.get('option_number', 1)
        upload_data = session.get('upload_data')
        if not upload_data:
            return jsonify({'error': 'No uploaded dataset found.'}), 400
        try:
            df = pd.DataFrame(upload_data)
        except Exception as e:
            logger.error(f"Error converting session upload_data to DataFrame: {str(e)}")
            return jsonify({'error': 'Uploaded dataset is invalid.'}), 400
        try:
            analyzer = AIAnalyzer(df)
            analysis_response = analyzer._execute_selected_analysis(option_number, analysis_prompt)
            return jsonify(analysis_response)
        except Exception as e:
            logger.error(f"Error in execute_ai_analysis endpoint: {str(e)}")
            return jsonify({'error': str(e)}), 500
    except Exception as e:
        logger.error(f"Execution failed: {str(e)}\nCode:\n{analysis_prompt}")
        return jsonify({'error': f"Visualization failed: {str(e)}"}), 500

@upload_chat.route('/upload', methods=['POST'])
def upload():
    try:
        if 'file' not in request.files:
            logger.error("No file in request")
            return jsonify({'error': 'No file uploaded'}), 400
            
        file = request.files['file']
        if not file or file.filename == '':
            return jsonify({'error': 'No selected file'}), 400

        # Validate extension
        if not file.filename.lower().endswith(('.csv', '.xlsx', '.xls')):
            return jsonify({'error': 'Unsupported file type'}), 400

        # Read file
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)

        # Clean data
        df = clean_data(df)
        
        # Store in session
        session['upload_data'] = df.to_dict(orient='records')
        
        # Convert NaN to None for JSON serialization
        preview_df = df.head(5).where(pd.notnull(df), None)
        
        session['upload_data_preview'] = {
            'columns': preview_df.columns.tolist(),
            'data': preview_df.to_dict(orient='records')
        }
        
        logger.debug(f"Preview data sample: {session['upload_data_preview']['data'][0]}")
        
        logger.info(f"Stored upload_data_preview with {len(df)} rows")
        logger.debug(f"Preview columns: {df.columns.tolist()}")
        logger.debug(f"First preview row: {df.iloc[0].to_dict()}")
        
        return jsonify({
            'redirect': url_for('upload_chat_bp.chat_upload')
        }), 200

    except Exception as e:
        logger.error("Upload failed: %s", str(e), exc_info=True)
        return jsonify({'error': 'File processing failed'}), 500

@upload_chat.route('/get_preview')
def get_preview():
    logger.info("GET /get_preview request received")
    logger.info(f"Session keys: {list(session.keys())}")
    
    preview_data = session.get('upload_data_preview', {})
    logger.info(f"Preview data exists: {'upload_data_preview' in session}")
    
    response = jsonify({
        'columns': preview_data.get('columns', []),
        'data': preview_data.get('data', [])
    })
    response.headers.add('Access-Control-Allow-Origin', '*')  # Temporary permissive CORS
    return response

@upload_chat.route('/chat_upload', methods=['GET'])
def chat_upload():
    """Render the main chat analysis interface"""
    try:
        # Get cleaned data from session
        upload_data = session.get('upload_data')
        if not upload_data:
            return redirect(url_for('upload_chat_bp.upload'))
            
        df = pd.DataFrame(upload_data)
        analyzer = AIAnalyzer(df)
        
        # Make sure we're passing the analysis dictionary
        return render_template('upload_chat.html',
                            analysis={
                                'total_rows': len(df),
                                'total_columns': len(df.columns),
                                'numeric_columns': analyzer.numeric_cols.tolist(),
                                'categorical_columns': analyzer.categorical_cols.tolist()
                            })
    
    except Exception as e:
        logger.error(f"Chat interface error: {str(e)}")
        # Pass an empty analysis dict to avoid template errors
        return render_template('upload_chat.html',
                             analysis={},  
                             error_message="Failed to initialize analysis interface"), 500

def validate_plotly_figure(fig):
    try:
        return to_json(fig)  # This validates the figure structure
    except Exception as e:
        raise ValueError(f"Invalid Plotly figure: {str(e)}")
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
import altair as alt
import json

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

class AIAnalyzer:
    def __init__(self, df, system_prompt_override=None):
        try:
            if not isinstance(df, pd.DataFrame):
                raise DataValidationError("Input must be a pandas DataFrame")
            if df.empty:
                raise DataValidationError("DataFrame is empty")
            
            self.df = df
            self.conversation_history = []
            self.client = OpenAI()
            
            self.numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
            self.categorical_cols = df.select_dtypes(include=['object']).columns.tolist()
            self.date_cols = [col for col in df.columns if pd.api.types.is_datetime64_any_dtype(df[col])]
            
            # Use the override prompt if supplied
            self.system_prompt = system_prompt_override if system_prompt_override is not None else ""
            
            # Optional: automatically convert 'datetime' columns if present.
            if 'datetime' in self.df.columns:
                self.df['datetime'] = pd.to_datetime(self.df['datetime'], errors='coerce')
            
            logger.info("Successfully initialized AIAnalyzer")
            
        except Exception as e:
            logger.error(f"Error in AIAnalyzer initialization: {str(e)}")
            raise AIAnalysisError(f"Failed to initialize analyzer: {str(e)}")

    def reset_state(self):
        self.conversation_history = []
        logger.info("Reset conversation history")

    def validate_dataframe(self, df):
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
            
            # Time series suggestions
            for date_col in self.date_cols:
                for num_col in self.numeric_cols:
                    suggestions['time_series'].append({
                        'type': 'line',
                        'x': date_col,
                        'y': num_col,
                        'title': f'{num_col} over time'
                    })

            # Correlation suggestions
            if len(self.numeric_cols) >= 2:
                suggestions['correlations'].append({
                    'type': 'heatmap',
                    'columns': self.numeric_cols.tolist(),
                    'title': 'Correlation Matrix'
                })

            # Distribution suggestions
            for num_col in self.numeric_cols:
                suggestions['distributions'].append({
                    'type': 'histogram',
                    'column': num_col,
                    'title': f'Distribution of {num_col}'
                })

            # Comparison suggestions
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

    def generate_predictions(self):
        """Generate predictions and forecasts"""
        try:
            predictions = {}
            
            # Simple linear regression for numeric columns
            for target_col in self.numeric_cols:
                other_cols = [col for col in self.numeric_cols if col != target_col]
                if other_cols:
                    X = self.df[other_cols]
                    y = self.df[target_col]
                    model = LinearRegression()
                    model.fit(X, y)
                    predictions[target_col] = {
                        'r2_score': float(model.score(X, y)),
                        'coefficients': {col: float(coef) for col, coef in zip(other_cols, model.coef_)}
                    }

            return predictions
        except Exception as e:
            logger.error(f"Error generating predictions: {str(e)}")
            return {}

    def generate_context_help(self, user_history=None):
        """Generate context-aware help and suggestions"""
        try:
            help_suggestions = {
                'data_quality': [],
                'analysis_suggestions': [],
                'visualization_suggestions': [],
                'advanced_insights': []
            }

            # Data quality suggestions
            missing_data = self.df.isnull().sum()
            if missing_data.any():
                help_suggestions['data_quality'].append({
                    'type': 'missing_data',
                    'message': f'Found {missing_data.sum()} missing values. Consider cleaning the data.',
                    'affected_columns': missing_data[missing_data > 0].index.tolist()
                })

            # Analysis suggestions based on data types
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

    def _generate_visualization(self, chart_type, analysis_result):
        chart = None
        try:
            if chart_type == "line":
                if analysis_result['x'] == "datetime":
                    x_field = "datetime:T"
                else:
                    x_field = analysis_result['x']

                chart = alt.Chart(self.df).mark_line(point=True).encode(
                    x=alt.X(x_field, title=analysis_result['x']),
                    y=alt.Y(analysis_result['y'], title=analysis_result['y']),
                    color=alt.Color(analysis_result['color']) if analysis_result.get('color') else alt.value('steelblue')
                ).properties(
                    title=f"Trend of {analysis_result['y']} over {analysis_result['x']}",
                    width=600,
                    height=400
                )
            elif chart_type == "bar":
                chart = alt.Chart(self.df).mark_bar().encode(
                    x=alt.X(analysis_result['x'], title=analysis_result['x']),
                    y=alt.Y(analysis_result['y'], title=analysis_result['y']),
                    color=alt.Color(analysis_result['x'])
                ).properties(
                    title=f"{analysis_result['y']} by {analysis_result['x']}",
                    width=600,
                    height=400
                )
            elif chart_type == "scatter":
                chart = alt.Chart(self.df).mark_circle(size=60).encode(
                    x=alt.X(analysis_result['x'], title=analysis_result['x']),
                    y=alt.Y(analysis_result['y'], title=analysis_result['y']),
                    color=alt.Color(analysis_result['x']),
                    tooltip=[analysis_result['x'], analysis_result['y']]
                ).properties(
                    title=f"{analysis_result['y']} vs {analysis_result['x']}",
                    width=600,
                    height=400
                )
        except Exception as e:
            print("Error generating Altair chart:", e)
            return None

        # Return the Vega‑Lite JSON specification
        return chart.to_json() if chart else None

    def generate_smart_response(self, user_message):
        try:
            if not user_message.strip():
                return {'error': 'Please enter a valid question'}
            
            # If the user input is numeric, treat it as a selection from previous suggestions.
            try:
                selected_option = int(user_message.strip())
                logger.info(f"User selected option {selected_option}")
                last_suggestions = None
                # Find the last AI message that contains valid JSON suggestions.
                for msg in reversed(self.conversation_history):
                    try:
                        suggestions = json.loads(msg.get('content', ''))
                        if isinstance(suggestions, list):
                            last_suggestions = suggestions
                            break
                    except Exception:
                        continue
                if last_suggestions is None:
                    return {'error': 'No previous suggestions found. Please ask for analysis suggestions first.'}
                return self._execute_selected_analysis(selected_option, last_suggestions)
            except ValueError:
                # Not a numeric input; continue generating a fresh suggestion.
                pass

            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_message}
            ]
            # Optionally, include recent conversation messages for context.
            if self.conversation_history:
                for msg in self.conversation_history[-3:]:
                    role = "assistant" if msg.get('type') == 'ai' else "user"
                    messages.append({"role": role, "content": msg.get('content', '')})

            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=messages,
                temperature=0.2
            )
            ai_response = response.choices[0].message.content
            logger.info("Generated AI response")
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
            return {'message': ai_response, 'type': 'success'}
        except Exception as e:
            logger.error(f"Error generating response: {str(e)}")
            return {'error': 'Could not generate response', 'details': str(e)}

    def _generate_dataset_description(self, df):
        try:
            description = "The data shows "
            
            # Check for time-related columns
            time_cols = [col for col in df.columns if any(term in col.lower() 
                        for term in ['year', 'date', 'time', 'period'])]
            
            # Get main numeric columns (excluding any ID/index columns)
            main_numeric = [col for col in self.numeric_cols 
                           if not any(term in col.lower() 
                           for term in ['id', 'index', 'code'])]
            
            # Get main categorical columns
            main_categorical = [col for col in self.categorical_cols 
                              if not any(term in col.lower() 
                              for term in ['id', 'code', 'index'])]
            
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

    def process_analysis_strategy(self, ai_response):
        try:
            start_marker = "=== ANALYSIS CODE BEGIN ==="
            end_marker = "=== ANALYSIS CODE END ==="
            start_index = ai_response.find(start_marker)
            if start_index == -1:
                raise ValueError("Start marker not found")
            start_index += len(start_marker)
            end_index = ai_response.find(end_marker, start_index)
            if end_index == -1:
                raise ValueError("End marker not found")
            json_text = ai_response[start_index:end_index].strip()
            return json.loads(json_text)
        except Exception as e:
            logger.error(f"Error processing analysis strategy: {str(e)}")
            return {'error': f"Failed to extract analysis configuration: {str(e)}"}

    def _execute_selected_analysis(self, option_number, analysis_prompt):
        try:
            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": analysis_prompt}
                ],
                temperature=0.2
            )
            ai_response = response.choices[0].message.content
            marker_phrase = "=== ANALYSIS CODE BEGIN ==="
            end_marker = "=== ANALYSIS CODE END ==="
            if marker_phrase in ai_response and end_marker in ai_response:
                start_idx = ai_response.find(marker_phrase) + len(marker_phrase)
                end_idx = ai_response.find(end_marker)
                if end_idx > start_idx:
                    code = ai_response[start_idx:end_idx].strip()
                    logger.info(f"Extracted code (length {len(code)}): {code}")
                    local_vars = {
                        'df': self.df,
                        'pd': pd,
                        'np': np,
                        'px': px,
                        'go': go
                    }
                    exec(code, None, local_vars)
                    result = local_vars.get('result')
                    if result is None:
                        logger.error("Execution succeeded but no result was produced")
                        raise AIAnalysisError("Analysis did not produce any results")
                    return {
                        'message': "Analysis complete",
                        'type': 'success',
                        'result': result,
                        'has_plot': isinstance(result, str) and len(result) > 1000
                    }
            else:
                logger.warning("Markers not found in AI response; code extraction skipped")
                return None
        except Exception as e:
            logger.error(f"Failed to execute analysis: {str(e)}")
            raise AIAnalysisError(f"Failed to execute analysis: {str(e)}")

    def get_visualization_config(self, selected_option):
        """
        Generates and returns a JSON configuration for the selected analysis option.
        The configuration is enclosed between the markers '=== CONFIG BEGIN ===' and '=== CONFIG END ==='.
        """
        try:
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": (
                    f"Generate a JSON configuration for the analysis option: '{selected_option}'. "
                    "Do not include any Altair vegafusion code. "
                    "Return only the JSON configuration between the markers '=== CONFIG BEGIN ===' and '=== CONFIG END ===', "
                    "with no extraneous commentary."
                )}
            ]
            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=messages,
                temperature=0.2
            )
            ai_response = response.choices[0].message.content
            return self._extract_config(ai_response)
        except Exception as e:
            logger.error(f"Error in get_visualization_config: {str(e)}")
            return None

    def _extract_config(self, response_text):
        try:
            start_marker = "=== CONFIG BEGIN ==="
            end_marker = "=== CONFIG END ==="
            start_index = response_text.find(start_marker)
            if start_index == -1:
                raise ValueError("Start marker not found in AI response")
            start_index += len(start_marker)
            end_index = response_text.find(end_marker, start_index)
            if end_index == -1:
                raise ValueError("End marker not found in AI response")
            config_text = response_text[start_index:end_index].strip()
            return json.loads(config_text)
        except Exception as e:
            logger.error(f"Error extracting config: {str(e)}")
            return None

    def generate_visualization(self, config):
        try:
            base_chart = alt.Chart(self.df).encode()
            if config['chart_type'] == 'line':
                chart = base_chart.mark_line().encode(
                    x=alt.X(config['encoding']['x'], type='temporal'),
                    y=alt.Y(config['encoding']['y'], type='quantitative')
                )
            elif config['chart_type'] == 'bar':
                chart = base_chart.mark_bar().encode(
                    x=alt.X(config['encoding']['x'], type='nominal'),
                    y=alt.Y(config['encoding']['y'], type='quantitative'),
                    color=alt.Color(config['encoding'].get('color', 'steelblue'))
                )
            else:
                raise ValueError("Unsupported chart type")
            return chart.to_json()
        except Exception as e:
            logger.error(f"Error generating visualization: {str(e)}")
            return None
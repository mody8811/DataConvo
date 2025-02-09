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
    def __init__(self, df):
        try:
            # Validate input
            if not isinstance(df, pd.DataFrame):
                raise DataValidationError("Input must be a pandas DataFrame")
            if df.empty:
                raise DataValidationError("DataFrame is empty")
            
            self.df = df
            self.conversation_history = []
            self.client = OpenAI()
            
            # Initialize column types with error handling
            try:
                self.numeric_cols = df.select_dtypes(include=[np.number]).columns
                self.categorical_cols = df.select_dtypes(include=['object']).columns
                self.date_cols = [col for col in df.columns if pd.api.types.is_datetime64_any_dtype(df[col])]
                
                # Generate dataset description
                dataset_description = self._generate_dataset_description(df)
                
                # Create dynamic column descriptions
                column_descriptions = []
                for col in df.columns:
                    sample_values = df[col].dropna().head(3).tolist()
                    sample_str = ", ".join(str(x) for x in sample_values)
                    column_descriptions.append(f"- {col} (e.g., {sample_str})")
                
                self.system_prompt = f"""You are an expert Python data analyst data analysis assistant analyzing a dataset:
- {len(self.df)} records
- Columns:
{chr(10).join(column_descriptions)}

{dataset_description}

When suggesting analyses:
1. Always format suggestions as numbered choices: "1. **Analysis Title**: Description"
2. Limit to 3-5 relevant choices based on the data
3. Make suggestions specific to the available columns


When providing code for analysis:
1. Always use plotly.express for visualizations (not matplotlib)
2. Start with the phrase "=== ANALYSIS CODE BEGIN ==="
3. Then provide the Python code, make sure its result = fig.to_json() not fig.show()
4. End with "=== ANALYSIS CODE END ==="

Example:
=== ANALYSIS CODE BEGIN ===
import plotly.express as px
fig = px.line(df, x='Year', y='Value')
result = fig.to_json()
=== ANALYSIS CODE END ===
5-Remmeber to always Convert the figure to JSON using result = fig.to_json()
"""
                logger.info("Successfully initialized AIAnalyzer")
                
            except Exception as e:
                logger.error(f"Error in column type initialization: {str(e)}")
                raise AIAnalysisError(f"Failed to initialize column types: {str(e)}")
            
        except Exception as e:
            logger.error(f"Error in AIAnalyzer initialization: {str(e)}")
            raise AIAnalysisError(f"Failed to initialize analyzer: {str(e)}")

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
                # Handle missing values
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
                    if len(self.df) > 2:  # Need at least 3 points for trend
                        # Sort by date
                        temp_df = self.df.sort_values(date_col)
                        # Calculate simple trend
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

    def generate_visualization(self, viz_type, config):
        """Generate visualization data"""
        try:
            if viz_type == 'line':
                return self._generate_line_plot(config)
            elif viz_type == 'heatmap':
                return self._generate_heatmap(config)
            elif viz_type == 'histogram':
                return self._generate_histogram(config)
            elif viz_type == 'box':
                return self._generate_box_plot(config)
            else:
                logger.error(f"Unknown analysis type: {viz_type}")
                raise AIAnalysisError(f"Unknown analysis type: {viz_type}")
        except Exception as e:
            logger.error(f"Error generating visualization: {str(e)}")
            raise AIAnalysisError(f"Error generating visualization: {str(e)}")

    def _generate_line_plot(self, config):
        """Generate line plot data"""
        try:
            x = self.df[config['x']].tolist()
            y = self.df[config['y']].tolist()
            return {'x': x, 'y': y, 'type': 'line', 'title': config['title']}
        except Exception as e:
            logger.error(f"Error generating line plot: {str(e)}")
            return None

    def _generate_heatmap(self, config):
        """Generate heatmap data"""
        try:
            corr_matrix = self.df[config['columns']].corr()
            return {
                'z': corr_matrix.values.tolist(),
                'x': config['columns'].tolist(),
                'y': config['columns'].tolist(),
                'type': 'heatmap',
                'title': config['title']
            }
        except Exception as e:
            logger.error(f"Error generating heatmap: {str(e)}")
            return None

    def _generate_histogram(self, config):
        """Generate histogram data"""
        try:
            values = self.df[config['column']].dropna().tolist()
            return {
                'x': values,
                'type': 'histogram',
                'title': config['title']
            }
        except Exception as e:
            logger.error(f"Error generating histogram: {str(e)}")
            return None

    def _generate_box_plot(self, config):
        """Generate box plot data"""
        try:
            data = []
            for category in self.df[config['x']].unique():
                values = self.df[self.df[config['x']] == category][config['y']].dropna().tolist()
                data.append({
                    'y': values,
                    'name': str(category),
                    'type': 'box'
                })
            return {
                'data': data,
                'title': config['title']
            }
        except Exception as e:
            logger.error(f"Error generating box plot: {str(e)}")
            return None

    def generate_smart_response(self, user_message):
        """Generate context-aware responses based on user input"""
        try:
            logger.info(f"Generating response for message: {user_message}")
            
            # Check if the message is a number (user selecting an option)
            try:
                selected_option = int(user_message.strip())
                logger.info(f"User selected option {selected_option}")
                # If it's a number, execute the last suggested analysis
                if self.conversation_history:
                    last_suggestions = self.conversation_history[-1].get('content', '')
                    # Execute the selected analysis
                    return self._execute_selected_analysis(selected_option, last_suggestions)
            except ValueError:
                # Not a number, continue with normal response generation
                pass
            
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_message}
            ]
            
            # Add conversation history for context
            if self.conversation_history:
                for msg in self.conversation_history[-3:]:  # Last 3 messages
                    role = "assistant" if msg.get('type') == 'ai' else "user"
                    messages.append({"role": role, "content": msg.get('content', '')})
            
            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=messages,
                temperature=0.2
            )
            
            ai_response = response.choices[0].message.content
            logger.info("Generated AI response")
            
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

    def _execute_selected_analysis(self, option_number, analysis_prompt):
        """Execute the selected analysis option"""
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
            logger.debug(f"Full AI Response: {ai_response}")
            
            marker_phrase = "=== ANALYSIS CODE BEGIN ==="
            end_marker = "=== ANALYSIS CODE END ==="
            
            if marker_phrase in ai_response and end_marker in ai_response:
                start_idx = ai_response.find(marker_phrase) + len(marker_phrase)
                end_idx = ai_response.find(end_marker)
                
                if end_idx > start_idx:
                    code = ai_response[start_idx:end_idx].strip()
                    logger.info(f"Extracted code to execute (length {len(code)}):\n{code}")
                    
                    # Set up environment and execute
                    local_vars = {
                        'df': self.df,
                        'pd': pd,
                        'np': np,
                        'px': px,
                        'go': go
                    }
                    logger.debug(f"Local variables before exec: {list(local_vars.keys())}")
                    
                    try:
                        exec(code, None, local_vars)
                        result = local_vars.get('result')
                        
                        if result is None:
                            logger.error("Code executed but no result was produced")
                            raise AIAnalysisError("Analysis did not produce any results")
                        
                        return {
                            'message': "Analysis complete",
                            'type': 'success',
                            'result': result,
                            'has_plot': isinstance(result, str) and len(result) > 1000
                        }
                    except Exception as exec_error:
                        logger.error(f"Error during code execution: {str(exec_error)}")
                        raise AIAnalysisError(f"Failed to execute analysis: {str(exec_error)}")
            
            else:
                logger.warning("End marker found before start marker. Code extraction skipped.")
                return None
            
        except Exception as e:
            logger.error(f"Failed to execute analysis: {str(e)}")
            raise AIAnalysisError(f"Failed to execute analysis: {str(e)}")

    def _generate_dataset_description(self, df):
        """Generate a description of the dataset based on its contents"""
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
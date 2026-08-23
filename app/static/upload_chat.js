/* exported sendDatasetQuery */

// Add console log to verify script loading
console.log("Upload chat JavaScript loaded");

document.addEventListener('DOMContentLoaded', function() {
    console.log("DOM loaded, attaching event listeners");
    
    const analyzeButton = document.getElementById('analyze-button');
    const customInput = document.getElementById('custom-analysis-input');
    const chatDisplay = document.getElementById('chat-display');

    function formatSummaryTable(data) {
        let html = '<div class="table-container">';
        
        // Dataset Overview Table
        html += `
            <table class="data-table">
                <thead>
                    <tr>
                        <th colspan="2">Dataset Overview</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>Total Rows</td>
                        <td>${data.column_info.total_rows}</td>
                    </tr>
                    <tr>
                        <td>Total Columns</td>
                        <td>${data.column_info.total_columns}</td>
                    </tr>
                </tbody>
            </table>`;

        // Numeric Columns Statistics
        for (const [column, stats] of Object.entries(data.basic_stats)) {
            html += `
                <table class="data-table">
                    <thead>
                        <tr>
                            <th colspan="2">${column} Statistics</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr><td>Mean</td><td>${Number(stats.mean).toLocaleString(undefined, {maximumFractionDigits: 2})}</td></tr>
                        <tr><td>Median</td><td>${Number(stats['50%']).toLocaleString(undefined, {maximumFractionDigits: 2})}</td></tr>
                        <tr><td>Std Dev</td><td>${Number(stats.std).toLocaleString(undefined, {maximumFractionDigits: 2})}</td></tr>
                        <tr><td>Min</td><td>${Number(stats.min).toLocaleString(undefined, {maximumFractionDigits: 2})}</td></tr>
                        <tr><td>Max</td><td>${Number(stats.max).toLocaleString(undefined, {maximumFractionDigits: 2})}</td></tr>
                    </tbody>
                </table>`;
        }

        html += '</div>';
        return html;
    }

    function formatMarkdownTable(markdownText) {
        // Check if text contains markdown table
        if (!markdownText.includes('|')) return markdownText;
        
        // Split into lines and filter empty lines
        const lines = markdownText.split('\n').filter(line => line.trim());
        let html = '<div class="table-container"><table class="data-table">';
        
        lines.forEach((line, index) => {
            // Skip separator line (----)
            if (line.includes('---')) return;
            
            const cells = line.split('|').filter(cell => cell.trim());
            const isHeader = index === 0;
            
            html += '<tr>';
            cells.forEach(cell => {
                html += isHeader 
                    ? `<th>${cell.trim()}</th>`
                    : `<td>${cell.trim()}</td>`;
            });
            html += '</tr>';
        });
        
        html += '</table></div>';
        return html;
    }

    function displayPlotlyVisualization(vizData, containerId) {
        const container = document.getElementById(containerId);
        if (!container) {
            console.error('Container not found:', containerId);
            return;
        }

        try {
            // Clean the visualization data before parsing
            const cleanVizData = vizData.replace(/\b(NaN|Infinity|-Infinity)\b/g, '0');
            
            let vizJson = JSON.parse(cleanVizData);

            // Replace any NaN/Infinity values in the data arrays
            if (vizJson.data) {
                vizJson.data.forEach(trace => {
                    ['x', 'y', 'z'].forEach(axis => {
                        if (Array.isArray(trace[axis])) {
                            trace[axis] = trace[axis].map(val => 
                                (val === null || val === undefined || 
                                 String(val).match(/^(NaN|Infinity|-Infinity)$/)) ? 0 : val
                            );
                        }
                    });
                });
            }

            Plotly.newPlot(container, vizJson.data, vizJson.layout)
                .catch(error => {
                    console.error('Plotly Error:', error);
                    displayVisualizationError(container, 'Failed to create visualization');
                });

        } catch (error) {
            console.error('Visualization Error:', error);
            displayVisualizationError(container, error.message);
        }
    }

    function displayAltairVisualization(vizData, containerId) {
        const container = document.getElementById(containerId);
        if (!container) {
            console.error('Container not found:', containerId);
            return;
        }
        try {
            let vizJson = JSON.parse(vizData);
            vegaEmbed(container, vizJson)
                .then(result => {
                    console.log("Vega chart rendered successfully:", result);
                })
                .catch(error => {
                    console.error("Error rendering Vega chart:", error);
                    container.innerHTML = `<div style="color:red;">Error rendering chart: ${error.message}</div>`;
                });
        } catch (err) {
            console.error("Failed to parse visualization JSON:", err);
            container.innerHTML = `<div style="color:red;">Invalid visualization specification</div>`;
        }
    }

    function displayVisualizationError(container, errorMessage) {
        container.innerHTML = `
            <div class="visualization-error">
                <i class="fas fa-exclamation-circle"></i>
                <p>Failed to load visualization: ${errorMessage}</p>
                <button onclick="retryVisualization('${container.id}')">Retry</button>
                <pre class="error-details" style="display:none"></pre>
            </div>
        `;
    }
    
    function retryVisualization(button) {
        const container = button.closest('.visualization');
        const vizData = container.dataset.vizData;
        container.innerHTML = '';
        displayPlotlyVisualization(vizData, container.id);
    }

    function handleVisualizationMessage(data, messageContent) {
        if (data.visualizations && Object.keys(data.visualizations).length > 0) {
            console.log("Processing visualizations:", data.visualizations);
            messageContent += `<div class="visualization-container">`;
            
            Object.entries(data.visualizations).forEach(([key, vizData]) => {
                const vizId = `viz-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
                // Parse to check if there are multiple plots, but come back to the raw string for injection.
                const isMultiPlot = JSON.parse(vizData).data.length > 1;
                
                messageContent += `
                    <div class="visualization-controls">
                        <button onclick="downloadVisualization('${vizId}', 'png')">
                            <i class="fas fa-download"></i> PNG
                        </button>
                        <button onclick="downloadVisualization('${vizId}', 'svg')">
                            <i class="fas fa-download"></i> SVG
                        </button>
                        <button onclick="toggleFullscreen('${vizId}')">
                            <i class="fas fa-expand"></i>
                        </button>
                    </div>
                    <div id="${vizId}" 
                         class="visualization ${isMultiPlot ? 'multi-plot' : ''}"
                         data-viz-data='${vizData}'>
                        <div class="visualization-loading">
                            <i class="fas fa-spinner fa-spin"></i> Loading visualization...
                        </div>
                    </div>`;
                
                // Initialize the plot after adding it to the DOM
                setTimeout(() => displayPlotlyVisualization(vizData, vizId), 0);
            });
            messageContent += `</div>`;
        }
        return messageContent;
    }

    // Utility functions for visualization controls
    function downloadVisualization(vizId, format) {
        const viz = document.getElementById(vizId);
        Plotly.downloadImage(viz, {
            format: format,
            height: viz.offsetHeight * 2,
            width: viz.offsetWidth * 2,
            scale: 2
        });
    }

    function toggleFullscreen(vizId) {
        const viz = document.getElementById(vizId);
        if (!document.fullscreenElement) {
            viz.requestFullscreen();
        } else {
            document.exitFullscreen();
        }
    }

    function handleError(error) {
        const errorMessages = {
            'validation_error': 'There was an issue with the data. Please check your file and try again.',
            'ai_error': 'The AI assistant encountered an issue. Please try rephrasing your request.',
            'unexpected_error': 'An unexpected error occurred. Please refresh the page and try again.'
        };

        const messageDiv = document.createElement('div');
        messageDiv.className = 'message error';
        messageDiv.innerHTML = `
            <div class="error-content">
                <i class="fas fa-exclamation-circle"></i>
                <div class="error-text">
                    ${errorMessages[error.category] || error.message}
                </div>
                ${error.category === 'unexpected_error' ? 
                    '<button onclick="location.reload()" class="retry-button">Refresh Page</button>' : 
                    '<button onclick="retryLastAction()" class="retry-button">Try Again</button>'}
            </div>
        `;
        
        document.getElementById('chat-display').appendChild(messageDiv);
    }

    // Shows a loading indicator message in the chat.
    function showLoadingIndicator() {
        console.log("Showing loading indicator...");
        // Append a temporary message for UI responsiveness.
        appendMessage('AI', "<em>Processing your request...</em>", true);
    }

    // Hides the loading indicator.
    // (Instead of actually removing it, our code replaces it with the actual AI reply.)
    function hideLoadingIndicator() {
        console.log("Hiding loading indicator...");
        // Optionally clear or update a status element here.
    }

    // Append a message to the chat display.
    function appendMessage(sender, text, isHTML = false) {
        const msgDiv = document.createElement('div');
        msgDiv.classList.add('message');

        const contentDiv = document.createElement('div');
        contentDiv.classList.add('message-content');

        if (isHTML) {
            contentDiv.innerHTML = text;
        } else {
            const senderLabel = document.createElement('strong');
            senderLabel.textContent = sender + ':';
            const textDiv = document.createElement('div');
            textDiv.classList.add('text');
            textDiv.textContent = text;
            contentDiv.appendChild(senderLabel);
            contentDiv.appendChild(textDiv);
        }

        msgDiv.appendChild(contentDiv);
        chatDisplay.appendChild(msgDiv);
        chatDisplay.scrollTop = chatDisplay.scrollHeight;
    }

    // Display analysis suggestions as clickable bubbles.
    function displayAnalysisOptions(options) {
        const container = document.createElement('div');
        container.classList.add('analysis-options-container');

        options.forEach((option, index) => {
            const bubble = document.createElement('div');
            bubble.classList.add('suggestion-bubble');
            bubble.textContent = option.description || `Option ${index+1}`;
            bubble.addEventListener('click', function() {
                executeSelectedAnalysis(option);
            });
            container.appendChild(bubble);
        });

        const chatDisplay = document.getElementById('chat-display');
        chatDisplay.appendChild(container);
        chatDisplay.scrollTop = chatDisplay.scrollHeight;
    }

    // Trigger a dedicated analysis request.
    async function executeSelectedAnalysis(option) {
        console.log("executeSelectedAnalysis called with:", option);
        appendMessage('AI', "<em>Executing selected analysis...</em>", true);
        try {
            const response = await fetch('/upload_chat/execute_ai_analysis', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    analysis_prompt: option.analysis_prompt,
                    option_number: option.option_number || 1
                })
            });
            const analysisData = await response.json();
            console.log("AI Analysis execution result:", analysisData);
            if (analysisData.visualization) {
                const vizContainer = document.createElement('div');
                vizContainer.classList.add('viz-plot');
                vizContainer.id = 'viz-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9);
                appendMessage('AI', "<em>Rendering analysis visualization...</em>", true);
                chatDisplay.appendChild(vizContainer);
                // Use Vega-Embed to render the Altair (Vega-Lite) visualization
                displayAltairVisualization(analysisData.visualization, vizContainer.id);
            } else if (analysisData.error) {
                appendMessage('AI', `<em>Error executing analysis: ${analysisData.error}</em>`, true);
            } else {
                appendMessage('AI', `<em>No visualization produced by analysis code.</em>`, true);
            }
        } catch (error) {
            console.error("Error executing analysis option:", error);
            appendMessage('AI', `<em>Error occurred while executing analysis.</em>`, true);
        }
    }

    // Function to extract analysis code from the AI response using regex
    function extractAnalysisCode(botReply) {
        // The regex below captures everything between the start and end markers,
        // accounting for additional whitespace or newlines.
        const analysisRegex = /===\s*ANALYSIS CODE BEGIN\s*===([\s\S]*?)===\s*ANALYSIS CODE END\s*===/;
        const match = botReply.match(analysisRegex);
        if (match) {
            const analysisCode = match[1].trim();
            console.log("Extracted analysis code:", analysisCode);
            return analysisCode;
        } else {
            console.warn("Analysis code markers not found or are misformatted.");
            return null;
        }
    }

    // Add message display functions
    function showGreeting(message) {
        const messageDiv = document.createElement('div');
        messageDiv.className = 'message ai';
        messageDiv.innerHTML = `
            <div class="message-content">
                <i class="fas fa-robot"></i>
                <div class="text">
                    ${marked.parse(message)}
                </div>
            </div>
        `;
        chatDisplay.appendChild(messageDiv);
        chatDisplay.scrollTop = chatDisplay.scrollHeight;
    }

    function showAnalysisSuggestions(suggestions) {
        const messageDiv = document.createElement('div');
        messageDiv.className = 'message ai';
        messageDiv.innerHTML = `
            <div class="message-content">
                <i class="fas fa-robot"></i>
                <div class="text">
                    ${marked.parse(suggestions)}
                </div>
            </div>
        `;
        chatDisplay.appendChild(messageDiv);
        chatDisplay.scrollTop = chatDisplay.scrollHeight;
    }

    function showAnalysisResult(content) {
        const messageDiv = document.createElement('div');
        messageDiv.className = 'message ai';
        messageDiv.innerHTML = `
            <div class="message-content">
                <i class="fas fa-robot"></i>
                <div class="text">
                    ${marked.parse(content)}
                </div>
            </div>
        `;
        chatDisplay.appendChild(messageDiv);
        chatDisplay.scrollTop = chatDisplay.scrollHeight;
    }

    function showError(error) {
        const messageDiv = document.createElement('div');
        messageDiv.className = 'message error';
        messageDiv.innerHTML = `
            <div class="message-content">
                <i class="fas fa-exclamation-circle"></i>
                <div class="text">
                    ${error}
                </div>
            </div>
        `;
        chatDisplay.appendChild(messageDiv);
        chatDisplay.scrollTop = chatDisplay.scrollHeight;
    }

    // Update the sendMessage function to use these display functions
    async function sendMessage() {
        console.log("sendMessage triggered");
        const userInput = document.getElementById('user-input');
        const chatDisplay = document.getElementById('chat-display');
        const message = userInput.value.trim();
        if (!message) {
            console.log("No message entered");
            return;
        }

        // Display user message
        const userMessageDiv = document.createElement('div');
        userMessageDiv.className = 'message user';
        userMessageDiv.innerHTML = `
            <div class="message-content">
                <i class="fas fa-user"></i>
                <div class="text">${message}</div>
            </div>
        `;
        chatDisplay.appendChild(userMessageDiv);
        
        // Clear input
        userInput.value = '';
        
        try {
            const response = await fetch('/upload_chat/chat', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({message})
            });
            
            const data = await response.json();
            console.log("Response from /upload_chat/chat:", data);
            
            if (!response.ok) {
                throw new Error(data.error || 'Unknown error');
            }

            // Handle different response types
            if (data.type === 'greeting') {
                showGreeting(data.message);
            } else if (data.type === 'suggestions') {
                showAnalysisSuggestions(data.message);
            } else {
                showAnalysisResult(data.message);
            }
            
        } catch (error) {
            showError(error.message);
        }
    }

    // Function to open visualization modal
    window.openVisualizationModal = function() {
        document.getElementById('visualization-modal').style.display = 'block';
        document.querySelector('.modal-overlay').style.display = 'block';
    };

    // Function to close visualization modal
    window.closeVisualizationModal = function() {
        document.getElementById('visualization-modal').style.display = 'none';
        document.querySelector('.modal-overlay').style.display = 'none';
    };

    // Function to handle tab switching
    window.showTab = function(tabName) {
        const tabs = document.querySelectorAll('.tab-content');
        const buttons = document.querySelectorAll('.tab-btn');

        tabs.forEach(tab => {
            tab.classList.remove('active');
        });

        buttons.forEach(button => {
            button.classList.remove('active');
        });

        document.getElementById(`${tabName}-tab`).classList.add('active');
        document.querySelector(`.tab-btn[onclick="showTab('${tabName}')"]`).classList.add('active');
    };

    // Function to handle suggested analysis
    function suggestAnalysis(suggestion) {
        console.log("Suggesting analysis:", suggestion);
        const userInput = document.getElementById('user-input');
        userInput.value = suggestion;
        sendMessage();
    }

    // Attach click event listeners to suggestion items
    document.querySelectorAll('#suggestions-list li').forEach(item => {
        item.addEventListener('click', function() {
            suggestAnalysis(this.textContent);
        });
    });

    const uploadForm = document.getElementById('uploadForm');
    if (uploadForm) {
        uploadForm.addEventListener('submit', function(e) {
            e.preventDefault();
            
            const formData = new FormData(this);
            
            fetch('/upload_chat/upload', {
                method: 'POST',
                body: formData
            })
            .then(response => {
                if (!response.ok) {
                    return response.text().then(text => {
                        console.error('Server error:', text);
                        throw new Error('Server returned non-OK status');
                    });
                }
                return response.json();
            })
            .then(data => {
                if (data.type === 'error') {
                    showError(data.message);
                } else {
                    handleUploadSuccess(data);
                }
            })
            .catch(error => {
                console.error('Error:', error);
                showError('Error uploading file: ' + error.message);
            });
        });
    } else {
        console.error("Upload form not found!");
    }

    function createActionButtons(actions) {
        console.log('Creating action buttons for:', actions);
        let buttonsHtml = '';
        
        actions.forEach((action, index) => {
            if (action.type === 'choice') {
                // Create button with direct sendMessage call
                buttonsHtml += `
                    <div class="choice-container">
                        <div class="choice-button" onclick="sendMessage('${action.text}')">
                            <span class="choice-number">${action.number}</span>
                            <div class="choice-content">
                                <span class="choice-text">${action.text}</span>
                                ${action.description ? `<span class="choice-description">${action.description}</span>` : ''}
                            </div>
                        </div>
                    </div>`;
            }
        });
        
        console.log('Generated buttons HTML:', buttonsHtml);
        return buttonsHtml;
    }

    function handleAction(action) {
        if (action.type === 'visualization') {
            // Send request to create visualization
            fetch('/create_visualization', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    chartType: action.type,
                    columns: action.columns
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    showToast(data.error, 'error');
                } else {
                    // Render the Altair chart in the designated container (e.g., with id 'chart-container')
                    displayAltairVisualization(data.chart, 'chart-container');
                    closeVisualizationModal();
                    showToast('Visualization created successfully!', 'success');
                }
            })
            .catch(error => {
                console.error('Error:', error);
                showToast(error.message, 'error');
            });
        } else if (action.type === 'analysis') {
            // Similar handling for analysis actions
        } else if (action.type === 'choice') {
            // Send the selected choice as a new message
            sendMessage(action.text);
        }
    }

    function updateDataPreview(data) {
        const previewContainer = document.getElementById('preview-container');
        if (!previewContainer) return;

        try {
            previewContainer.innerHTML = ''; // Clear existing content
            const table = document.createElement('table');
            table.className = 'preview-table';

            // Create header
            const thead = document.createElement('thead');
            const headerRow = document.createElement('tr');
            data.columns.forEach(col => {
                const th = document.createElement('th');
                th.textContent = col || '';
                headerRow.appendChild(th);
            });
            thead.appendChild(headerRow);
            table.appendChild(thead);

            // Create body with the first few rows
            const tbody = document.createElement('tbody');
            data.slice(0, 5).forEach(row => {
                const tr = document.createElement('tr');
                data.columns.forEach(col => {
                    const td = document.createElement('td');
                    td.textContent = row[col] !== null && row[col] !== undefined ? row[col].toString() : '';
                    tr.appendChild(td);
                });
                tbody.appendChild(tr);
            });
            table.appendChild(tbody);

            previewContainer.appendChild(table);
        } catch (error) {
            console.error('Error updating preview:', error);
            previewContainer.innerHTML = `
                <div class="error-message">
                    <i class="fas fa-exclamation-circle"></i>
                    Failed to load preview: ${error.message}
                </div>
            `;
        }
    }

    function handleUploadSuccess(response) {
        try {
            console.log("Upload response:", response);
            if (!response) {
                throw new Error('No response from server');
            }

            // Clean any NaN values from the preview data
            if (response.preview) {
                const cleanPreview = {
                    columns: response.preview.columns,
                    data: response.preview.data.map(row => {
                        const cleanRow = {};
                        Object.keys(row).forEach(key => {
                            let value = row[key];
                            // Convert NaN/Infinity/-Infinity to null
                            if (value === "NaN" || value === "Infinity" || value === "-Infinity" || 
                                (typeof value === 'number' && (isNaN(value) || !isFinite(value)))) {
                                cleanRow[key] = null;
                            } else {
                                cleanRow[key] = value;
                            }
                        });
                        return cleanRow;
                    })
                };
                updateDataPreview(cleanPreview);
            } else {
                // Fetch preview separately
                fetch('/upload_chat/get_preview')
                    .then(res => res.json())
                    .then(data => {
                        console.log("Fetched preview:", data);
                        // Clean the fetched data
                        const cleanData = safeJSONParse(JSON.stringify(data));
                        updateDataPreview(cleanData);
                    })
                    .catch(err => {
                        console.error('Error fetching preview:', err);
                        showError('Failed to load data preview');
                    });
            }
        } catch (error) {
            console.error('Error handling upload success:', error);
            showError('Failed to process uploaded data');
        }
    }

    function showError(message) {
        const errorDiv = document.createElement('div');
        errorDiv.className = 'message error';
        errorDiv.innerHTML = `
            <div class="error-content">
                <i class="fas fa-exclamation-circle"></i>
                <div class="error-text">
                    ${message}
                </div>
                <button onclick="retryLastAction()" class="retry-button">Try Again</button>
            </div>
        `;
        
        document.getElementById('chat-display').appendChild(errorDiv);
    }

    // Function to load the preview data and update the UI
    const loadPreview = async () => {
        const loadingEl = document.getElementById('preview-loading');
        const tableEl = document.getElementById('preview-table');
        
        try {
            // Always start with clean slate
            tableEl.innerHTML = '';
            loadingEl.style.display = 'flex';
            
            const response = await fetch('/get_preview');
            if (!response.ok) throw new Error('Failed to fetch');
            
            const { columns, data } = await response.json();
            
            // Create table structure
            const thead = tableEl.createTHead();
            const headerRow = thead.insertRow();
            columns.forEach(col => {
                const th = document.createElement('th');
                th.textContent = col;
                headerRow.appendChild(th);
            });

            // Populate first 5 rows
            const tbody = tableEl.createTBody();
            data.slice(0, 5).forEach(row => {
                const tr = tbody.insertRow();
                columns.forEach(col => {
                    const td = tr.insertCell();
                    td.textContent = row[col] || '—';
                });
            });
            
        } catch (error) {
            console.error('Preview failed:', error);
            tableEl.innerHTML = 
                `<tr><td colspan="${columns.length}">Preview unavailable</td></tr>`;
        } finally {
            loadingEl.style.display = 'none';
            tableEl.style.display = 'table'; // Force show regardless of content
        }
    }

    // Call loadPreview when the page loads
    document.addEventListener('DOMContentLoaded', () => {
        // Add delay to ensure elements exist
        setTimeout(loadPreview, 100); 
        
        // Add debug logging
        console.log('Preview table element:', document.getElementById('preview-table'));
        console.log('Loading element:', document.getElementById('preview-loading'));
    });

    function safeJSONParse(str) {
        try {
            return JSON.parse(str.replace(/\b(NaN|Infinity|-Infinity)\b/g, "null"));
        } catch(e) {
            console.error('JSON Parse Error:', e);
            return null;
        }
    }

    function updatePreview(data) {
        const container = document.getElementById('preview-container');
        if (!container) return;

        try {
            const previewData = safeJSONParse(data);
            if (!previewData) {
                throw new Error('Invalid preview data');
            }

            // Create table
            const table = document.createElement('table');
            table.className = 'preview-table';
            
            // Add headers
            const thead = document.createElement('thead');
            const headerRow = document.createElement('tr');
            previewData.columns.forEach(column => {
                const th = document.createElement('th');
                th.textContent = column || '';
                headerRow.appendChild(th);
            });
            thead.appendChild(headerRow);
            table.appendChild(thead);

            // Add data rows
            const tbody = document.createElement('tbody');
            previewData.data.slice(0, 5).forEach(row => {
                const tr = document.createElement('tr');
                previewData.columns.forEach(column => {
                    const td = document.createElement('td');
                    const value = row[column];
                    td.textContent = row[column] !== null && row[column] !== undefined ? row[column].toString() : '';
                    tr.appendChild(td);
                });
                tbody.appendChild(tr);
            });
            table.appendChild(tbody);

            // Clear and update container
            container.innerHTML = '';
            container.appendChild(table);

        } catch (error) {
            console.error('Preview Error:', error);
            container.innerHTML = `
                <div class="error-message">
                    <i class="fas fa-exclamation-circle"></i>
                    Failed to load preview: ${error.message}
                </div>
            `;
        }
    }

    function formatInsightsTable(insights) {
        let html = '<table class="insights-table"><thead><tr><th>Category</th><th>Type</th><th>Title</th><th>Details</th></tr></thead><tbody>';
        insights.forEach(insight => {
            let details = '';
            for (const key in insight.details) {
                details += `<strong>${key}</strong>: ${insight.details[key]}<br>`;
            }
            html += `<tr>
                        <td>${insight.category}</td>
                        <td>${insight.type}</td>
                        <td>${insight.title}</td>
                        <td>${details}</td>
                    </tr>`;
        });
        html += '</tbody></table>';
        return html;
    }

    function getInsights() {
        fetch('/get_insights', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                showToast(data.error, 'error');
            } else {
                // Format insights into a nicely styled table.
                const insightsHTML = formatInsightsTable(data.insights);
                // Insert the table into a designated container; for example, a modal or a div with id "insights-container"
                const container = document.getElementById('insights-container');
                if (container) {
                    container.innerHTML = insightsHTML;
                } else {
                    const newContainer = document.createElement('div');
                    newContainer.id = 'insights-container';
                    newContainer.innerHTML = insightsHTML;
                    document.body.appendChild(newContainer);
                }
            }
        })
        .catch(error => {
            console.error('Error fetching insights:', error);
            showToast('Error fetching insights', 'error');
        });
    }

    // Function to render the suggestions as clickable options
    function renderAnalysisTable(suggestions) {
        const container = document.createElement('div');
        
        if (!suggestions || suggestions.length === 0) {
            container.innerHTML = `
                <div class="alert alert-info">
                    No analysis options found. Try asking something like:
                    "Show me sales trends by region"
                </div>
            `;
            return container;
        }
        
        const tableHTML = `
            <table class="analysis-table">
                ${suggestions.map((s, i) => `
                    <tr class="analysis-option" data-option="${i + 1}">
                        <td>${i + 1}</td>
                        <td>${s.title ? s.title : s}</td>
                        <td>${s.description ? s.description : 'No description'}</td>
                    </tr>
                `).join('')}
            </table>
        `;
        
        container.innerHTML = tableHTML;
        return container;
    }

    // Function to display errors in the chat output
    function displayError(msg) {
        const chatDisplay = document.getElementById('chat-display');
        const errorDiv = document.createElement('div');
        errorDiv.className = 'message error';
        errorDiv.textContent = `[AI]: ${msg}`;
        chatDisplay.appendChild(errorDiv);
    }

    // Function to render the visualization (analysis result) on the page
    function renderVisualization(visualization) {
        // For this example, we display the visualization configuration as a formatted JSON code block.
        const chatDisplay = document.getElementById('chat-display');
        const visDiv = document.createElement('div');
        visDiv.className = 'message ai';
        visDiv.innerHTML = `<pre>${JSON.stringify(visualization, null, 2)}</pre>`;
        chatDisplay.appendChild(visDiv);
    }

    // Main function to handle the analysis flow
    async function handleAnalysisFlow() {
        try {
            // Get initial analysis suggestions from the backend
            const suggResponse = await fetch('/upload_chat/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: 'get_suggestions' })
            });
            const suggData = await suggResponse.json();
            
            if (suggData.error) {
                displayError(suggData.error);
                return;
            }
            
            if (suggData.analysis_options) {
                // Render the suggestion buttons in a dedicated container
                const container = renderAnalysisTable(suggData.analysis_options);
                // Assume there's an element (with id "analysis-container") where we place the options
                document.getElementById('analysis-container').innerHTML = "";
                document.getElementById('analysis-container').appendChild(container);
            
                // Add event listeners for each option
                document.querySelectorAll('.analysis-option').forEach(option => {
                    option.addEventListener('click', async () => {
                        // Retrieve the option id (or text, as appropriate)
                        const chosenOption = option.getAttribute('data-option');
                        try {
                            // Request visualization (actual analysis) for the selected option.
                            const visResponse = await fetch('/upload_chat/chat', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({
                                    action: 'get_visualization',
                                    option: chosenOption
                                })
                            });
                            const visData = await visResponse.json();
                            if (visData.error) {
                                displayError(visData.error);
                            } else if (visData.visualization) {
                                renderVisualization(visData.visualization);
                            } else {
                                displayError("No visualization received.");
                            }
                        } catch (err) {
                            console.error('Visualization error:', err);
                            displayError("Visualization request failed.");
                        }
                    });
                });
            } else {
                displayError("No analysis options were returned from the server.");
            }
        } catch (err) {
            console.error('Error fetching analysis suggestions:', err);
            displayError("Unable to connect to the analysis server.");
        }
    }

    // Sends the dataset query to the backend to get analysis suggestions.
    async function sendDatasetQuery() {
        console.log("sendDatasetQuery triggered");
        const customInput = document.getElementById('custom-analysis-input');
        const chatDisplay = document.getElementById('chat-display');
        
        if (!customInput) {
            console.error("custom-analysis-input element not found!");
            return;
        }
        
        const userMessage = customInput.value.trim();
        if (!userMessage) {
            console.log("No message provided in custom analysis input.");
            return;
        }
        console.log("User message:", userMessage);
        
        // (Optional) Display user message if chatDisplay exists
        if (chatDisplay) {
             const userMessageDiv = document.createElement('div');
             userMessageDiv.className = 'message user';
             userMessageDiv.innerHTML = `
                 <div class="message-content">
                     <i class="fas fa-user"></i>
                     <div class="text">${userMessage}</div>
                 </div>
             `;
             chatDisplay.appendChild(userMessageDiv);
             chatDisplay.scrollTop = chatDisplay.scrollHeight;
        } else {
             console.warn("chat-display element not found; user message not appended.");
        }
        
        // Clear the input field
        customInput.value = '';
        
        // Send request to the server
        try {
            const response = await fetch('/upload_chat/chat', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ message: userMessage })
            });
            
            const data = await response.json();
            console.log("Response from /upload_chat/chat:", data);
            
            if (!response.ok) {
                console.error("Server responded with an error:", data);
                return;
            }
            
            // You can add additional handling of data here,
            // e.g., calling a function to display the response.
                
        } catch (error) {
             console.error("Error during fetch:", error);
        }
    }

    // Renders the analysis suggestions as clickable buttons arranged vertically.
    function renderAnalysisSuggestions(suggestions) {
        console.log("Received suggestions:", suggestions);  // Debugging line to check what is received

        const container = document.getElementById('analysis-container') || document.createElement('div');
        container.id = 'analysis-container';
        container.innerHTML = ''; // Clear previous content

        suggestions.forEach((suggestion, index) => {
            console.log("Processing suggestion:", suggestion);  // Debugging line to check each suggestion
            const button = document.createElement('button');
            button.className = 'analysis-option btn btn-primary';
            button.style.display = 'block';
            button.style.width = '100%';
            button.style.margin = '8px 0';

            // Set the button text to the full description of the suggestion
            button.textContent = suggestion; // Assuming suggestions are plain strings as per debug log
            button.onclick = function() { fetchAnalysis(suggestion); };
            container.appendChild(button);
        });

        const chatDisplay = document.getElementById('chat-display');
        chatDisplay.appendChild(container);
    }

    // Attach listeners so that clicking a suggestion button fetches visualization.
    function attachAnalysisOptionListeners() {
        const buttons = document.querySelectorAll('.analysis-option');
        buttons.forEach(btn => {
            btn.addEventListener('click', async function() {
                const optionIndex = this.getAttribute('data-option');
                try {
                    const response = await fetch('/upload_chat/chat', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        // Use action "get_visualization" and pass the chosen option.
                        body: JSON.stringify({ action: 'get_visualization', option: optionIndex })
                    });
                    const visData = await response.json();
                    if (visData.error) {
                        displayError(visData.error);
                    } else if (visData.visualization) {
                        renderVisualization(visData.visualization);
                    } else {
                        displayError("No visualization received.");
                    }
                } catch (err) {
                    console.error(err);
                    displayError("Visualization request failed.");
                }
            });
        });
    }

    window.sendDatasetQuery = sendDatasetQuery;

    if (analyzeButton && customInput) {
        analyzeButton.addEventListener('click', function() {
            console.log("Analyze button clicked");
            sendDatasetQuery();
        });
        customInput.addEventListener('keypress', function(event) {
            if (event.key === 'Enter') {
                console.log("Enter key pressed in custom-analysis-input");
                sendDatasetQuery();
            }
        });
        console.log("Event listeners attached to analyze button and custom input");
    } else {
        console.error("Analyze button or custom analysis input not found");
    }

    // NEW: Attach event listeners for the chat send button and chat input
    const chatSendButton = document.getElementById('send-button');
    const chatInput = document.getElementById('user-input');
    
    if (chatSendButton && chatInput) {
        chatSendButton.addEventListener('click', function() {
            console.log("Send button clicked");
            sendMessage();
        });
        chatInput.addEventListener('keypress', function(event) {
            if (event.key === 'Enter') {
                console.log("Enter key pressed in chat input");
                sendMessage();
            }
        });
        console.log("Event listeners attached to chat send button and chat input");
    } else {
        console.error("Chat send button or chat input not found");
    }

    const populatePreviewTable = (columns, data) => {
        const table = document.getElementById('preview-table');
        table.innerHTML = ''; // Clear existing content
        
        // Add missing header row creation
        const thead = table.createTHead();
        const headerRow = thead.insertRow();
        columns.forEach(col => {
            const th = document.createElement('th');
            th.textContent = col;
            headerRow.appendChild(th);
        });

        // Add body content
        const tbody = table.createTBody();
        data.slice(0, 5).forEach(row => { // Show first 5 rows
            const tr = tbody.insertRow();
            columns.forEach(col => {
                const td = tr.insertCell();
                td.textContent = row[col] || '';
            });
        });
        table.appendChild(thead);
        table.appendChild(tbody);
    };
});
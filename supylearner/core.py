from sklearn import clone, metrics
from sklearn.base import BaseEstimator, RegressorMixin
import sklearn.cross_validation as cv
import numpy as np
import pandas as pd
from scipy.optimize import fmin_l_bfgs_b, nnls, fmin_slsqp
import time


class SLError(Exception):
    """
    Base class for errors in the SupyLearner package
    """
    pass


class SuperLearner(BaseEstimator):
    """
    Loss-based super learning

    SuperLearner chooses a weighted combination of candidate estimates
    in a specified library using cross-validation.

    Parameters
    ----------
    library : list
        List of scikit-learn style estimators with fit() and predict()
        methods.

    K : Number of folds for cross-validation.

    loss : loss function, 'L2' or 'nloglik'.

    discrete : True to choose the best estimator
               from library ("discrete SuperLearner"), False to choose best
               weighted combination of esitmators in the library.

    coef_method : Method for estimating weights for weighted combination
                  of estimators in the library. 'L_BFGS_B', 'NNLS', or 'SLSQP'.

    stratifyCV : True to create folds containing approximately the same percentage 
                    of samples of each target class as the complete set 
                    during cross-validation steps.

	shuffleCV : Whether to shuffle the data before determining cross-validation folds. 


    Attributes
    ----------

    n_estimators : number of candidate estimators in the library.

    coef : Coefficients corresponding to the best weighted combination
           of candidate estimators in the libarary. 

    risk_cv : List of cross-validated risk estimates for each candidate
              estimator, and the (not cross-validated) estimated risk for
              the SuperLearner

    Examples
    --------

    from supylearner import *
    from sklearn import datasets, svm, linear_model, neighbors, svm
    import numpy as np

    #Generate a dataset.
    np.random.seed(100)
    X, y=datasets.make_friedman1(1000)

    ols=linear_model.LinearRegression()
    elnet=linear_model.ElasticNetCV(l1_ratio=.1) 
    ridge=linear_model.RidgeCV()
    lars=linear_model.LarsCV()
    lasso=linear_model.LassoCV()
    nn=neighbors.KNeighborsRegressor()
    svm1=svm.SVR() 
    svm2=svm.SVR(kernel='poly')
    lib=[ols, elnet, ridge,lars, lasso, nn, svm1, svm2]
    libnames=["OLS", "ElasticNet", "Ridge", "LARS", "LASSO", "kNN", "SVM rbf", "SVM poly"]

    sl=SuperLearner(lib, libnames, loss="L2")

    sl.fit(X, y)

    sl.summarize()

    """
    
    def __init__(self, library, libnames=None, K=5, loss='L2', discrete=False, coef_method='SLSQP',\
                 save_pred_cv=False, bound=0.00001, stratifyCV=False, shuffleCV=True):
        self.library=library[:]
        self.libnames=libnames
        self.K=K
        self.loss=loss
        self.discrete=discrete
        self.coef_method=coef_method
        self.n_estimators=len(library)
        self.save_pred_cv=save_pred_cv
        self.bound=bound
        self.stratifyCV=stratifyCV
        self.shuffleCV=shuffleCV
    
    def fit(self, X, y):
        """
        Fit SuperLearner.

        Parameters
        ----------
        X : numpy array of shape [n_samples,n_features]
            or other object acceptable to the fit() methods
            of all candidates in the library        
            Training data
        y : numpy array of shape [n_samples]
            Target values
        Returns
        -------
        self : returns an instance of self.
        """
      
        n=len(y)
        if self.stratifyCV==False:
            folds = cv.KFold(n, self.K, shuffle=self.shuffleCV) #ordinary cross-validation
        elif self.stratifyCV==True:
            folds = cv.StratifiedKFold(y, self.K, shuffle=self.shuffleCV) #balance outcome in folds

        #get cv predictions for each algorithm in library
        y_pred_cv = np.empty(shape=(n, self.n_estimators))
        fold_num = 0
        for train_index, test_index in folds:
            if isinstance(X, pd.DataFrame):
                #pandas DFs expect indices to refer to column labels unless with iloc 
                X_train, X_test=X.iloc[train_index], X.iloc[test_index]
            else:
                X_train, X_test=X[train_index], X[test_index]
            y_train, y_test=y[train_index], y[test_index]
            #print "Fold", fold_num
            for aa in range(self.n_estimators):
                est=clone(self.library[aa])
                start_time = time.time()
                est.fit(X_train,y_train)
                time_elapsed = (time.time()-start_time)/60
                #print "execution time for ", self.library[aa].__class__.__name__ , time_elapsed, " minutes" 
                y_pred_cv[test_index, aa]=self._get_pred(est, X_test)
            fold_num+=1
    
        #get SL coefficients
        self.coef=self._get_coefs(y, y_pred_cv)

        #fit each algorithm on entire dataset
        self.fitted_library=clone(self.library) #what is this clone function?
        for est in self.fitted_library:
            est.fit(X, y)
            
        #obtain risks of each algorithm (including SL) using the cv predictions
            #predictions used for SL are just the convex combo of algorithm's cv predictions
            #therefore, SL risk is not cross-validated
        self.risk_cv=[]
        for aa in range(self.n_estimators):
            self.risk_cv.append(self._get_risk(y, y_pred_cv[:,aa]))
        self.risk_cv.append(self._get_risk(y, self._get_combination(y_pred_cv, self.coef)))

        if self.save_pred_cv:
            self.y_pred_cv=y_pred_cv

        return self
                        
    
    def predict_proba(self, X):
        """
        Predict using SuperLearner

        Parameters
        ----------
        X : numpy.array of shape [n_samples, n_features]
           or other object acceptable to the predict() methods
           of all candidates in the library


        Returns
        -------
        array, shape = [n_samples]
           Array containing the predicted class labels.
        """
        
        n_X = X.shape[0]
        y_pred_all = np.empty((n_X,self.n_estimators))
        for aa in range(self.n_estimators):
            y_pred_all[:,aa]=self._get_pred(self.fitted_library[aa], X)
        y_pred=self._get_combination(y_pred_all, self.coef)
        return y_pred


    def summarize(self):
        """
        Print CV risk estimates for each candidate estimator in the library,
        coefficients for weighted combination of estimators,
        and estimated risk for the SuperLearner.

        Parameters
        ----------

        None

        Returns
        -------

        Nothing
        
        """
        if self.libnames is None:
            libnames=[est.__class__.__name__ for est in self.library]
        else:
            libnames=self.libnames
        print "Cross-validated risk estimates for each estimator in the library:"
        print np.column_stack((libnames, self.risk_cv[:-1]))
        print "\nCoefficients:"
        print np.column_stack((libnames,self.coef))
        print "\n(Not cross-valided) estimated risk for SL:", self.risk_cv[-1]

        

    def _get_combination(self, y_pred_mat, coef):
        """
        Calculate weighted combination of predictions

        Parameters
        ----------

        y_pred_mat: numpy.array of shape [X.shape[0], len(self.library)]
                    where each column is a vector of predictions from each candidate
                    estimator

        coef: numpy.array of length len(self.library), to be used to combine
              columns of y_pred_mat

        Returns
        _______

        comb: numpy.array of length X.shape[0] of predictions.
        
        
        """
        if self.loss=='L2':
            comb=np.dot(y_pred_mat, coef)
        elif self.loss=='nloglik':
            comb=_inv_logit(np.dot(_logit(_trim(y_pred_mat, self.bound)), coef))
        return comb

    def _get_risk(self, y, y_pred):
        """
        Calculate risk given observed y and predictions

        Parameters
        ----------
        y: numpy array of observed outcomes

        y_pred: numpy array of predicted outcomes of the same length

        Returns
        -------
        risk: estimated risk of y and predictions.
        
        """
        if self.loss=='L2':
            risk=np.mean((y-y_pred)**2)
        elif self.loss=='nloglik':
            risk=-np.mean( y   *   np.log(_trim(y_pred, self.bound))+\
                         (1-y)*np.log(1-(_trim(y_pred, self.bound))) )
        return risk
        
    def _get_coefs(self, y, y_pred_cv):
        """
        Find coefficients that minimize the estimated risk.

        Parameters
        ----------
        y: numpy.array of observed oucomes

        y_pred_cv: numpy.array of shape [len(y), len(self.library)] of cross-validated
                   predictions

        Returns
        _______
        coef: numpy.array of normalized non-negative coefficents to combine
              candidate estimators
              
        
        """
        if self.coef_method is 'L_BFGS_B':
            if self.loss=='nloglik':
                raise SLError("coef_method 'L_BFGS_B' is only for 'L2' loss")            
            def ff(x):
                return self._get_risk(y, self._get_combination(y_pred_cv, x))
            x0=np.array([1./self.n_estimators]*self.n_estimators)
            bds=[(0,1)]*self.n_estimators
            coef_init,b,c=fmin_l_bfgs_b(ff, x0, bounds=bds, approx_grad=True)
            if c['warnflag'] is not 0:
                raise SLError("fmin_l_bfgs_b failed when trying to calculate coefficients")
            
        elif self.coef_method is 'NNLS':
            if self.loss=='nloglik':
                raise SLError("coef_method 'NNLS' is only for 'L2' loss")
            coef_init, b=nnls(y_pred_cv, y)

        elif self.coef_method is 'SLSQP':
            def ff(x):
                return self._get_risk(y, self._get_combination(y_pred_cv, x))
            def constr(x):
                return np.array([ np.sum(x)-1 ]) #coefs should sum to 1
            x0=np.array([1./self.n_estimators]*self.n_estimators) #initial guess
            bds=[(0,1)]*self.n_estimators #each coef should be between 0 and 1, inclusive
            coef_init, b, c, d, e = fmin_slsqp(ff, x0, f_eqcons=constr, 
                bounds=bds, disp=0, full_output=1)
            if d is not 0:
                print "SL optimization exit code: " , d
                raise SLError("fmin_slsqp failed when trying to calculate coefficients")

        else: raise ValueError("method not recognized")
        coef_init = np.array(coef_init)
        #All coefficients should be non-negative or possibly a very small negative number,
        #But setting small values to zero makes them nicer to look at and hardly changes anything
        coef_init[coef_init < np.sqrt(np.finfo(np.double).eps)] = 0
        #Coefficients should already sum to (almost) one if method is 'SLSQP', 
        #and should be really close for the other methods if loss is 'L2' anyway.
        coef = coef_init/np.sum(coef_init)
        return coef

    def _get_pred(self, est, X):
        """
        Get prediction from the estimator.
        Use est.predict if loss is L2.
        If loss is nloglik, use est.predict_proba if possible
        otherwise just est.predict, which hopefully returns something
        like a predicted probability, and not a class prediction.
        """
        if self.loss == 'L2':
            pred=est.predict(X)
        if self.loss == 'nloglik':
            if hasattr(est, "predict_proba"):
                if est.__class__.__name__ == "SVC":
                    pred=est.predict_proba(X)[:, 0]
                else:
                    pred=est.predict_proba(X)[:, 1]
            else:
                pred=est.predict(X)
                if pred.min() < 0 or pred.max() > 1:
                    raise SLError("Probability less than zero or greater than one")
        return pred

def _trim(p, bound):
    """
    Trim a probabilty to be in (bound, 1-bound)

    Parameters
    ----------
    p: numpy.array of numbers (generally between 0 and 1)

    bound: small positive number <.5 to trim probabilities to

    Returns
    -------
    Trimmed p
    """
    p[p<bound]=bound
    p[p>1-bound]=1-bound
    return p

def _logit(p):
    """
    Calculate the logit of a probability
    
    Paramters
    ---------
    p: numpy.array of numbers between 0 and 1

    Returns
    -------
    logit(p)
    """
    return np.log(p/(1-p))

def _inv_logit(x):
    """
    Calculate the inverse logit

    Paramters
    ---------
    x: numpy.array of real numbers

    Returns
    -------
    iverse logit(x)

    """
    
    return 1/(1+np.exp(-x))
    
    
        
    
#could probably get rid of the cv_superlearner function in future since 
    #the sklearn cross_validation module will do the same thing
    #with consistency to other learners in python
def cv_superlearner(sl, X, y, K=5, stratifyCV=False, shuffleCV=True):
    """
    Cross validate the SuperLearner sl as well as all candidates in
    sl.library and print results.

    Parameters
    ----------

    sl: An object of type SuperLearner

    X : numpy array of shape [n_samples,n_features]
        or other object acceptable to the fit() methods
        of all candidates in the library        
        Training data
        
    y : numpy array of shape [n_samples]
        Target values

    K : Number of folds for cross-validating sl and candidate estimators.  More yeilds better result
        because training sets are closer in size to the full data-set, but more takes longer.

	stratifyCV : Whether to maintain balance of outcome in cross-validation folds.  Default=False.

	shuffleCV : Whether to shuffle the data before determining cross-validation folds. Default=True.

    
    Returns
    -------

    risks_cv: numpy array of shape [len(sl.library)] 

    """
    library = sl.library[:]

    n=len(y)
    if stratifyCV==False:
        folds = cv.KFold(n, K, shuffle=shuffleCV) #ordinary cross-validation
    elif stratifyCV==True:
        folds = cv.StratifiedKFold(y, K, shuffle=shuffleCV) #balance outcome in folds
    y_pred_cv = np.empty(shape=(n, len(library)+1))
    

    for train_index, test_index in folds:
        if isinstance(X, pd.DataFrame):
            #pandas DFs expect indices to refer to column labels unless with iloc 
            X_train, X_test=X.iloc[train_index], X.iloc[test_index]
        else:
            X_train, X_test=X[train_index], X[test_index]
        y_train, y_test=y[train_index], y[test_index]
        #fit each algorithm to this fold and get cv predictions
            #this will give us y_pred_cv, from which we will calculate risks
            #technically, this is redundant with what we calculated when running initial (non-CV) SL
        for aa in range(len(library)):
            est=library[aa]
            est.fit(X_train,y_train)
            y_pred_cv[test_index, aa]=sl._get_pred(est, X_test)
        #fit SL to this fold of data (will initiate additional split+fits of algorithms)
        sl.fit(X_train, y_train)
        y_pred_cv[test_index, len(library)]=sl.predict_proba(X_test)

    #use predicted values to get risk estimates (for each estimator and for SL)
    risk_cv=np.empty(shape=(len(library)+1, 1))
    for aa in range(len(library)+1):
        #List for risk for each fold for estimator aa
        risks=[]
        for train_index, test_index in folds:
            risks.append(sl._get_risk(y[test_index], y_pred_cv[test_index, aa]))
        #Take mean across volds
        risk_cv[aa]= np.mean(risks)

    #use predicted values to get other measures of performance
    

    if sl.libnames is None:
        libnames=[est.__class__.__name__ for est in sl.library]
    else:
        libnames=sl.libnames[:]
    libnames.append("SuperLearner")

    print "Cross-validated risk estimates for each estimator in the library and SuperLearner:"
    print np.column_stack((libnames, risk_cv))
    return risk_cv
    

    
